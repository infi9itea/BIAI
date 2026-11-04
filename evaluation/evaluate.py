"""M6 evaluation runner.

    python evaluation/evaluate.py live                     # REAL Gemini calls on all ten packs (needs GEMINI_API_KEY)
    python evaluation/evaluate.py live --packs 01 09       # a partial run (reported as partial, never as N/10)
    python evaluation/evaluate.py live --model gemini-3.8-flash   # the M6 run: always name the model explicitly
    python evaluation/evaluate.py rescore evaluation/runs/live-<id>   # re-score a saved live run offline
    python evaluation/evaluate.py fixtures                 # offline regression of the saved fixtures (NOT an LLM accuracy measure)

The three modes are kept apart on purpose:

* ``live`` makes the real API calls. Without a key it stops (exit 2). It NEVER falls back to fixtures, and a pack
  whose call fails counts as a failure in every metric.
* ``rescore`` re-runs the pipeline and the scorer on the raw responses a live run saved. No API call, and it is
  reported as RESCORE, never as a live run. Before it starts it compares the original run's input hashes, system
  prompt hash and pipeline-source hashes with the files on disk; if anything differs it refuses (exit 2), unless
  ``--allow-changed-inputs`` is given, in which case the output is labelled as a NEW EVALUATION of the old responses
  against changed inputs/code, not an exact reproduction. The original run id and prompt are preserved in the metadata.
* ``fixtures`` replays ``fixtures/mock_llm_responses`` through the same pipeline and scorer. It checks the code, not
  the model: the fixtures were captured earlier (model and version not recorded here), so its numbers are written
  to a separately named run and labelled as such.

Each run writes ``evaluation/runs/<mode>-<timestamp>/`` with ``metadata.json`` (model, prompt hash, config, git
state, input hashes), ``raw/`` (every model response, verbatim), ``results/`` (the PackResult per pack),
``scores.json`` and ``report.md``. ``ground_truth.json`` and ``field_ground_truth.json`` are only ever read.
"""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from evaluation import scoring  # noqa: E402
from main import InputError, find_packs, load_purchase_request  # noqa: E402
from src import extractor, pdf_reader  # noqa: E402
from src.models import PackResult  # noqa: E402
from src.pdf_reader import PDFReadError  # noqa: E402
from src.workflow import build_error_result, run_pipeline  # noqa: E402

DATA_DIR = REPO_ROOT / "assessment-data"
FIXTURE_DIR = REPO_ROOT / "fixtures" / "mock_llm_responses"
RUNS_DIR = REPO_ROOT / "evaluation" / "runs"
INPUT_FILES = ("purchase-request.csv", "company-rules.md")

LIVE, RESCORE, FIXTURES = "live", "rescore", "fixture_regression"
REPRODUCTION_EXACT, REPRODUCTION_CHANGED = "EXACT", "CHANGED_INPUTS_NEW_EVALUATION"
PackRun = scoring.PackRun


# ---------------------------------------------------------------------------------------------
# Clients injected into run_pipeline(client=...)
# ---------------------------------------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _setting(config, name):
    return config.get(name) if isinstance(config, dict) else getattr(config, name, None)


class _Response:
    """What extractor._generate reads from a response: only ``.text``."""

    def __init__(self, text: str) -> None:
        self.text = text


class RecordingClient:
    """Wraps the real Gemini client and records every call: request identity, config, the verbatim response text,
    the model version the API reports, token usage and latency. Changes nothing about the call."""

    def __init__(self, inner) -> None:
        self.attempts: list[dict] = []
        self.models = self
        self._inner = inner

    def generate_content(self, *, model, config, contents):
        record = {"requested_model": model, "contents_sha256": _sha(contents),
                  "system_instruction_sha256": _sha(_setting(config, "system_instruction") or ""),
                  "temperature": _setting(config, "temperature"),
                  "response_mime_type": _setting(config, "response_mime_type"),
                  "started_utc": datetime.now(timezone.utc).isoformat()}
        start = time.monotonic()
        try:
            response = self._inner.models.generate_content(model=model, config=config, contents=contents)
        except Exception as exc:
            record.update(ok=False, error=f"{type(exc).__name__}: {exc}", latency_s=round(time.monotonic() - start, 3))
            self.attempts.append(record)
            raise
        usage = getattr(response, "usage_metadata", None)
        record.update(
            ok=True, latency_s=round(time.monotonic() - start, 3), response_text=getattr(response, "text", None),
            reported_model_version=getattr(response, "model_version", None),
            response_id=getattr(response, "response_id", None),
            usage={k: getattr(usage, k, None) for k in ("prompt_token_count", "candidates_token_count",
                                                        "thoughts_token_count", "total_token_count")} if usage else None)
        self.attempts.append(record)
        return response


class ReplayClient:
    """Serves recorded responses in order, one per call. A recorded failure is raised again, so a pack that failed
    live fails identically on rescore. Never reaches the network."""

    def __init__(self, attempts: list[dict]) -> None:
        self.attempts = list(attempts)
        self.models = self
        self._next = 0
        # Where the request now being built differs from the one recorded (document text + prompt hashes). Empty for
        # an exact replay and for fixtures, whose records carry no hashes.
        self.drift: list[dict] = []

    def _note_drift(self, call: int, record: dict, config, contents) -> None:
        for what, recorded, now in (
                ("contents_sha256", record.get("contents_sha256"), _sha(contents)),
                ("system_instruction_sha256", record.get("system_instruction_sha256"),
                 _sha(_setting(config, "system_instruction") or ""))):
            if recorded is not None and recorded != now:
                self.drift.append({"call": call, "what": what, "recorded": recorded, "now": now})

    def generate_content(self, *, model, config, contents):
        if self._next >= len(self.attempts):
            raise RuntimeError("replay exhausted: no more recorded responses for this pack")
        record = self.attempts[self._next]
        self._note_drift(self._next + 1, record, config, contents)
        self._next += 1
        if not record.get("ok"):
            # re-raise under the recorded exception's own type name so the pipeline's error detail is identical
            type_name, _, message = (record.get("error") or "RuntimeError: recorded failure").partition(": ")
            raise type(type_name, (Exception,), {})(message)
        return _Response(record["response_text"])


# ---------------------------------------------------------------------------------------------
# Running one pack
# ---------------------------------------------------------------------------------------------


def run_pack(pdf_path: Path, pr, client) -> tuple[PackRun, list[dict]]:
    """Run the real pipeline on one pack and collect what the scorer needs. Never raises."""
    try:
        result = run_pipeline(str(pdf_path), pr, client=client)
    except Exception as exc:  # same net as main.process_pack: an unexpected exception is a failed pack, not a crash
        result = build_error_result(pdf_path.name, "unexpected_exception", f"{type(exc).__name__}: {exc}")
    attempts = list(getattr(client, "attempts", []))
    raw = None
    for attempt in attempts:  # the response call_llm used = the first recorded one that parses
        if attempt.get("ok") and attempt.get("response_text"):
            try:
                raw = extractor._parse_raw(attempt["response_text"])
                break
            except Exception:  # noqa: BLE001 - invalid JSON: call_llm retried, so look at the next attempt
                continue
    try:
        pdf_result = pdf_reader.extract_text(str(pdf_path))
    except PDFReadError:
        pdf_result = None
    if result.processing_status == "OK" and raw is None:  # cannot happen unless the pipeline changed; fail loudly
        raise RuntimeError(f"{pdf_path.name}: pipeline reported OK but no parseable model response was recorded")
    return scoring.PackRun(pdf_path.name, result, raw, pdf_result, attempts), attempts



# ---------------------------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------------------------


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_hashes() -> dict:
    """Hashes of the code that turns a model response into a result. ``pipeline`` (main.py and src/) decides whether a
    rescore is an exact reproduction; ``evaluation`` (the scorer and runner) is recorded for the record only, because
    re-scoring with a corrected scorer is the point of a rescore."""
    def group(files: list[Path]) -> dict:
        return {str(f.relative_to(REPO_ROOT)).replace("\\", "/"): _file_sha(f) for f in sorted(files)}

    return {"pipeline": group([REPO_ROOT / "main.py", *(REPO_ROOT / "src").glob("*.py")]),
            "evaluation": group(list((REPO_ROOT / "evaluation").glob("*.py")))}


def _current_input_hashes(packs: list[Path]) -> dict:
    gt_files = {"decision": scoring.DECISION_GT_PATH, "field": scoring.FIELD_GT_PATH}
    return {**{k: _file_sha(p) for k, p in {"ground_truth.json": gt_files["decision"],
                                           "field_ground_truth.json": gt_files["field"]}.items()},
            **{name: _file_sha(DATA_DIR / name) for name in INPUT_FILES},
            **{p.name: _file_sha(p) for p in packs}}


def collect_metadata(mode: str, packs: list[Path], all_attempts: dict[str, list[dict]], started: str, finished: str,
                     run_id: str, requested_model: str | None, original: dict | None = None) -> dict:
    """``original`` is the metadata of the live run being rescored. Its model, generation config and prompt are the
    ones that PRODUCED the responses, so a rescore keeps them rather than recording whatever is on disk now."""
    gt_files = {"decision": scoring.DECISION_GT_PATH, "field": scoring.FIELD_GT_PATH}
    versions = sorted({a["reported_model_version"] for att in all_attempts.values() for a in att
                       if a.get("reported_model_version")})
    try:
        sdk = importlib_metadata.version("google-genai")
    except importlib_metadata.PackageNotFoundError:
        sdk = None
    system_instruction = extractor.SYSTEM_INSTRUCTION
    meta = {
        "run_id": run_id, "mode": mode, "started_utc": started, "finished_utc": finished,
        "partial_run": len(packs) != len(list(DATA_DIR.glob("supplier-pack-*.pdf"))),
        "model": {
            "requested": requested_model,
            "reported_by_api": versions or None,
            "note": ("model and version were not recorded when these fixtures were captured" if mode == FIXTURES else None),
        },
        "generation_config": {"temperature": 0, "response_mime_type": "application/json",
                              "system_instruction_sha256": _sha(system_instruction), "max_attempts": extractor.MAX_ATTEMPTS,
                              "retry_delay_s": extractor.RETRY_DELAY_SECONDS},
        "prompt": {"system_instruction_sha256": _sha(system_instruction),
                   "contents_sha256_per_pack": {p: sorted({a["contents_sha256"] for a in att if "contents_sha256" in a})
                                                for p, att in all_attempts.items()}},
        "calls": {p: [{k: a.get(k) for k in ("ok", "error", "latency_s", "reported_model_version", "usage", "started_utc")}
                      for a in att] for p, att in all_attempts.items()},
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "google_genai": sdk},
        "git": {"commit": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain")),
                "ground_truth_last_commit": {k: _git("log", "-1", "--format=%H %cI", "--", str(p.relative_to(REPO_ROOT)))
                                             for k, p in gt_files.items()},
                "ground_truth_modified_in_working_tree": bool(_git("status", "--porcelain", "--",
                                                                  *[str(p.relative_to(REPO_ROOT)) for p in gt_files.values()]))},
        "input_sha256": _current_input_hashes(packs),
        "source_sha256": _source_hashes(),
    }
    if original is not None:  # keep what produced the responses; the current prompt goes in the rescore block
        for key in ("generation_config", "prompt"):
            meta[key] = original.get(key, meta[key])
    return meta


# ---------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------


def _pct(r: dict) -> str:
    return f"{r['n']}/{r['of']} ({r['pct']}%)" if r["of"] else "n/a"


def _rescore_banner(info: dict) -> str:
    lines = ["**RESCORE - offline replay of a previously recorded live run.** "
             "No Gemini API calls were made during this run.", "",
             f"- Original live run: `{info['original_run_id']}` (`{info['original_run_dir']}`), "
             f"started {info['original_started_utc']}, finished {info['original_finished_utc']}",
             f"- Model of the original run: requested `{info['original_model_requested']}`, "
             f"reported by API `{info['original_model_reported_by_api']}`",
             f"- System prompt of the original run: sha256 `{info['original_system_instruction_sha256']}` "
             f"(current file: `{info['current_system_instruction_sha256']}`)"]
    if info["reproduction"] == REPRODUCTION_EXACT:
        lines.append("- Reproduction: **EXACT** - input files, system prompt and pipeline source are byte-identical "
                     "to the original run; only the scorer may differ.")
    else:
        lines.append("- Reproduction: **NOT EXACT - NEW EVALUATION of the recorded responses against changed "
                     "inputs/code.** Differences from the original run:")
        lines += [f"    - {d['item']}: original `{d['original']}`, now `{d['current']}`" for d in info["differences"]]
        lines += [f"    - call {d['call']} {d['what']} differs from the recorded request" for d in info["call_drift"]]
    return "\n".join(lines)


def render_report(meta: dict, agg: dict, packs: list[dict]) -> str:
    mode = meta["mode"]
    rescore = meta.get("rescore")
    banner = {
        LIVE: "**LIVE RUN** - real Gemini API calls; a failed call is counted as a failure, never replaced by a fixture.",
        RESCORE: _rescore_banner(rescore) if rescore else "**RESCORE** - offline replay of a previously recorded live run.",
        FIXTURES: "**FIXTURE REGRESSION - NOT A LIVE LLM MEASURE.** Saved fixtures replayed through the pipeline; "
                  "this checks the code against the answer key, not the model. Do not quote these numbers as extraction accuracy.",
    }[mode]
    if meta["partial_run"]:
        banner += "\n\n**PARTIAL RUN** - fewer than ten packs; no N/10 figure is valid."
    m, p, d = agg["extraction_model_view"], agg["extraction_pipeline_view"], agg["decision"]
    lines = [f"# Evaluation run `{meta['run_id']}`", "", banner, "",
             f"- Model requested: `{meta['model']['requested']}`; reported by API: `{meta['model']['reported_by_api']}`",
             f"- Commit: `{meta['git']['commit']}` (dirty: {meta['git']['dirty']}); "
             f"ground truth modified in working tree: {meta['git']['ground_truth_modified_in_working_tree']}",
             f"- Started {meta['started_utc']}, finished {meta['finished_utc']}; SDK google-genai {meta['environment']['google_genai']}",
             "", "## Headline: four separate measures", "",
             "| Measure | Result |", "|---|---|",
             f"| Extraction - model output, field values correct | {_pct(m['fields_correct'])} |",
             f"| Extraction - after evidence verification, field values correct | {_pct(p['fields_correct'])} |",
             f"| Evidence - quotes verbatim on the cited page | {_pct(agg['evidence']['verbatim_on_claimed_page'])} |",
             f"| Evidence - quote supports the correct value | {_pct(agg['evidence']['supports_correct_value'])} |",
             f"| Evidence - quote supports the value the model claimed | {_pct(agg['evidence']['supports_claimed_value'])} |",
             f"| Arithmetic - check status agrees with expected | {_pct(agg['arithmetic']['status_agrees'])} |",
             f"| Arithmetic - printed value read by the pipeline matches the PDF | {_pct(agg['arithmetic']['printed_agrees'])} |",
             f"| Arithmetic - status agrees, excluding checks inheriting an extraction error in their own inputs | "
             f"{_pct(agg['arithmetic']['excluding_checks_inherited_from_extraction']['status_agrees'])} |",
             f"| Rules - exact rule set matches ground truth | {_pct(agg['rules']['packs_with_exact_rule_set'])} |",
             f"| Rules - exact rule set including the line each per-item rule fired on | "
             f"{_pct(agg['rules']['packs_with_exact_rule_items'])} |",
             f"| Line items - fabricated (beyond the answer key) / returned | "
             f"{agg['line_items']['spurious']} / {agg['line_items']['returned_by_model']} |",
             f"| **Decision agreement** (processing failures count as wrong) | {_pct(d['agreement'])} |",
             f"| Decision agreement with no other error in the pack | {_pct(d['agreeing_with_no_other_error'])} |",
             "", "Decision agreement is not extraction accuracy: a correct decision can rest on a wrong intermediate value. "
             f"Right decision, wrong intermediate: {d['right_decision_wrong_intermediate'] or 'none'}.",
             "", "## Extraction detail (model view)", "",
             f"- Value recall (fields that have a value in the PDF): {_pct(m['value_recall'])}",
             f"- Value precision (values returned that are right): {_pct(m['value_precision'])}",
             f"- Outcomes: {m['outcomes']}",
             f"- Verifier false accepts / false rejects (judged against the model's CLAIMED value and its quote): "
             f"{agg['evidence']['verifier_false_accepts']} / {agg['evidence']['verifier_false_rejects']}; "
             f"verified fields whose claim is wrong per the key: {agg['evidence']['verified_but_claim_wrong']}; "
             f"verified fields whose claim could not be checked automatically: "
             f"{agg['evidence']['verified_claim_uncheckable']}",
             "", "| Field | " + " | ".join(["CORRECT", "CORRECT_NULL", "WRONG", "MISSED", "SPURIOUS", "NOT_EXTRACTED"]) + " |",
             "|---|" + "---|" * 6]
    for name, counts in agg["extraction_per_field"].items():
        lines.append(f"| {name} | " + " | ".join(str(counts.get(o, 0)) for o in
                     ["CORRECT", "CORRECT_NULL", "WRONG", "MISSED", "SPURIOUS", "NOT_EXTRACTED"]) + " |")
    lines += ["", "## Per pack", "", "| Pack | Expected | Pipeline | Agrees | Expected rules | Triggered rules | Status |",
              "|---|---|---|---|---|---|---|"]
    for s in packs:
        lines.append(f"| {s['pack']} | {s['expected_decision']} | {s['decision']} | {'yes' if s['decision_agrees'] else 'NO'} | "
                     f"{', '.join(s['expected_rules']) or '-'} | {', '.join(s['triggered_rules']) or '-'} | {s['processing_status']} |")
    lines += ["", "Confusion (rows = expected, columns = pipeline; processing failures excluded): "
              f"`{d['confusion_expected_by_pipeline']}`", "", "## Failure analysis", ""]
    failures = [f for s in packs for f in s["failures"]]
    lines.append(f"Counts: `{agg['failure_counts']}`" if failures else "No discrepancies.")
    lines.append("")
    for f in failures:
        lines.append(f"- **{f['pack']}** [{f['level']}] {f['category']}: {f['detail']}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------------------------


def _select_packs(selection: list[str] | None) -> list[Path]:
    if not selection:
        return find_packs(DATA_DIR)
    return [find_packs(DATA_DIR, f"supplier-pack-{int(s):02d}.pdf")[0] for s in selection]


def _prompt_text(meta: dict) -> str:
    """The prompt to archive with a run: for a rescore, the ORIGINAL run's saved prompt, not whatever is on disk now."""
    info = meta.get("rescore")
    if info:
        saved = Path(info["original_run_dir"]) / "prompt" / "system_instruction.txt"
        if saved.is_file():
            return saved.read_text(encoding="utf-8")
    return extractor.SYSTEM_INSTRUCTION


def _write_run(run_dir: Path, meta: dict, runs: list[PackRun], attempts: dict[str, list[dict]], scores: list[dict],
               agg: dict, save_raw: bool = True) -> None:
    (run_dir / "results").mkdir(parents=True, exist_ok=True)
    if save_raw:
        (run_dir / "raw").mkdir(parents=True, exist_ok=True)
        (run_dir / "prompt").mkdir(exist_ok=True)
        (run_dir / "prompt" / "system_instruction.txt").write_text(_prompt_text(meta), encoding="utf-8")
        for pack, att in attempts.items():
            (run_dir / "raw" / f"{Path(pack).stem}.json").write_text(
                json.dumps({"pack": pack, "attempts": att}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for run in runs:
        (run_dir / "results" / f"{Path(run.pack).stem}.json").write_text(
            run.result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (run_dir / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (run_dir / "scores.json").write_text(
        json.dumps({"aggregate": agg, "packs": scores}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (run_dir / "report.md").write_text(render_report(meta, agg, scores), encoding="utf-8")


def _evaluate(packs: list[Path], pr, client_for, mode: str, requested_model: str | None, run_dir: Path,
              original: dict | None = None, rescore_info: dict | None = None) -> tuple[dict, dict]:
    decision_gt, field_gt = scoring.load_decision_gt(), scoring.load_field_gt()
    started = datetime.now(timezone.utc).isoformat()
    runs, attempts, drift = [], {}, []
    for path in packs:
        print(f"  {path.name} ...", flush=True)
        client = client_for(path)
        run, att = run_pack(path, pr, client)
        runs.append(run)
        attempts[path.name] = att
        drift += [{"pack": path.name, **d} for d in getattr(client, "drift", [])]
    scores = [scoring.score_pack(r, decision_gt[r.pack], field_gt[r.pack], pr) for r in runs]
    agg = scoring.aggregate(scores)
    meta = collect_metadata(mode, packs, attempts, started, datetime.now(timezone.utc).isoformat(), run_dir.name,
                            requested_model, original)
    if rescore_info is not None:
        rescore_info["call_drift"] = drift  # a request that no longer matches the recorded one is never exact
        if drift:
            rescore_info["reproduction"] = REPRODUCTION_CHANGED
        meta["rescore"] = rescore_info
    _write_run(run_dir, meta, runs, attempts, scores, agg, save_raw=(mode != FIXTURES))
    return meta, agg


def _new_run_dir(prefix: str) -> Path:
    return RUNS_DIR / f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def cmd_live(args) -> int:
    import os

    if not os.getenv("GEMINI_API_KEY"):
        print("error: GEMINI_API_KEY is not set. A live evaluation needs the real API; it will not fall back to "
              "fixtures. Copy .env.example to .env and add your key.", file=sys.stderr)
        return 2
    if args.model:
        extractor.GEMINI_MODEL = args.model  # read at call time by extractor._generate
    packs = _select_packs(args.packs)
    pr = load_purchase_request(DATA_DIR / "purchase-request.csv")
    inner = extractor._get_client()
    run_dir = _new_run_dir("live")
    print(f"live evaluation: model {extractor.GEMINI_MODEL}, {len(packs)} pack(s) -> {run_dir}")
    meta, agg = _evaluate(packs, pr, lambda _p: RecordingClient(inner), LIVE, extractor.GEMINI_MODEL, run_dir)
    return _finish(run_dir, meta, agg)


def compare_with_original(old: dict) -> list[dict]:
    """Everything that could make a rescore differ from the live run it claims to reproduce: input files, system
    prompt and pipeline source. Empty list = byte-identical. Anything the original run did not record is reported as
    a difference too, since 'not recorded' cannot support a claim of exact reproduction."""
    differences: list[dict] = []

    def check(item: str, original, current) -> None:
        if original != current:
            differences.append({"item": item, "original": original, "current": current})

    paths = {"ground_truth.json": scoring.DECISION_GT_PATH, "field_ground_truth.json": scoring.FIELD_GT_PATH,
             **{name: DATA_DIR / name for name in INPUT_FILES}, **{name: DATA_DIR / name for name in old["calls"]}}
    for name, recorded in sorted(old.get("input_sha256", {}).items()):
        path = paths.get(name, DATA_DIR / name)
        check(f"input file {name}", recorded, _file_sha(path) if path.is_file() else "MISSING")
    for name in sorted(set(paths) - set(old.get("input_sha256", {}))):
        check(f"input file {name}", "not recorded", "present")
    check("system prompt sha256", old.get("generation_config", {}).get("system_instruction_sha256"),
          _sha(extractor.SYSTEM_INSTRUCTION))
    recorded_src = old.get("source_sha256", {}).get("pipeline")
    current_src = _source_hashes()["pipeline"]
    if recorded_src is None:
        check("pipeline source", "not recorded", "present")
    else:
        for name in sorted(set(recorded_src) | set(current_src)):
            check(f"pipeline source {name}", recorded_src.get(name, "absent"), current_src.get(name, "absent"))
    return differences


def cmd_rescore(args) -> int:
    source = Path(args.run_dir)
    old = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    if old["mode"] != LIVE:
        print(f"error: {source} is not a live run (mode {old['mode']})", file=sys.stderr)
        return 2
    differences = compare_with_original(old)
    if differences and not args.allow_changed_inputs:
        print(f"error: refusing to rescore {source.name}: it would not reproduce the original run, because "
              f"{len(differences)} thing(s) differ from it:", file=sys.stderr)
        for d in differences:
            print(f"  - {d['item']}: original {d['original']}, now {d['current']}", file=sys.stderr)
        print("Restore the original inputs/code, or pass --allow-changed-inputs to run a NEW evaluation of the "
              "recorded responses against the changed inputs (it is labelled as such, never as a reproduction).",
              file=sys.stderr)
        return 2
    extractor.RETRY_DELAY_SECONDS = 0
    extractor.GEMINI_MODEL = old["model"]["requested"] or extractor.GEMINI_MODEL  # the model that produced the responses
    packs = [DATA_DIR / name for name in sorted(old["calls"])]
    recorded = {n: json.loads((source / "raw" / f"{Path(n).stem}.json").read_text(encoding="utf-8"))["attempts"]
                for n in old["calls"]}
    pr = load_purchase_request(DATA_DIR / "purchase-request.csv")
    out = Path(args.out) if args.out else source / "rescored"
    info = {
        "original_run_id": old["run_id"], "original_run_dir": str(source),
        "original_started_utc": old["started_utc"], "original_finished_utc": old["finished_utc"],
        "original_git_commit": (old.get("git") or {}).get("commit"),
        "original_model_requested": old["model"]["requested"],
        "original_model_reported_by_api": old["model"]["reported_by_api"],
        "original_system_instruction_sha256": old.get("generation_config", {}).get("system_instruction_sha256"),
        "current_system_instruction_sha256": _sha(extractor.SYSTEM_INSTRUCTION),
        "original_input_sha256": old.get("input_sha256"),
        "original_source_sha256": old.get("source_sha256"),
        "reproduction": REPRODUCTION_CHANGED if differences else REPRODUCTION_EXACT,
        "differences": differences, "call_drift": [],
    }
    meta, agg = _evaluate(packs, pr, lambda p: ReplayClient(recorded[p.name]), RESCORE, old["model"]["requested"], out,
                          original=old, rescore_info=info)
    code = _finish(out, meta, agg)
    if meta["rescore"]["reproduction"] != REPRODUCTION_EXACT:
        print("NOTE: not an exact reproduction of the original run; see the report banner.", file=sys.stderr)
        return code or (0 if args.allow_changed_inputs else 3)
    return code


def cmd_fixtures(args) -> int:
    extractor.RETRY_DELAY_SECONDS = 0
    packs = _select_packs(args.packs)
    pr = load_purchase_request(DATA_DIR / "purchase-request.csv")

    def client_for(path: Path) -> ReplayClient:
        text = (FIXTURE_DIR / f"{path.stem}.json").read_text(encoding="utf-8")
        return ReplayClient([{"ok": True, "response_text": text}])

    run_dir = _new_run_dir("fixtures")
    print(f"FIXTURE REGRESSION (not a live measure): {len(packs)} pack(s) -> {run_dir}")
    meta, agg = _evaluate(packs, pr, client_for, FIXTURES, None, run_dir)
    return _finish(run_dir, meta, agg)


def _finish(run_dir: Path, meta: dict, agg: dict) -> int:
    d = agg["decision"]
    print(f"\n{meta['mode'].upper()} run {run_dir.name}: decision agreement {_pct(d['agreement'])} "
          f"(processing failures counted wrong: {d['processing_failures_counted_incorrect']})")
    print(f"  fields correct, model view   {_pct(agg['extraction_model_view']['fields_correct'])}")
    print(f"  fields correct, after verify {_pct(agg['extraction_pipeline_view']['fields_correct'])}")
    print(f"  arithmetic status agrees     {_pct(agg['arithmetic']['status_agrees'])}")
    print(f"  exact rule sets              {_pct(agg['rules']['packs_with_exact_rule_set'])}")
    print(f"report: {run_dir / 'report.md'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    live = sub.add_parser("live", help="real Gemini calls (needs GEMINI_API_KEY); never uses fixtures")
    live.add_argument("--packs", nargs="+", metavar="NN", help="pack numbers, e.g. 01 09 (partial run)")
    live.add_argument("--model", help="model for this run (recorded in metadata); the M6 run uses gemini-3.8-flash")
    rescore = sub.add_parser("rescore", help="re-score a saved live run offline")
    rescore.add_argument("run_dir")
    rescore.add_argument("--out", help="output folder (default: <run_dir>/rescored)")
    rescore.add_argument("--allow-changed-inputs", action="store_true",
                         help="rescore even though inputs/prompt/pipeline differ from the original run; the result is "
                              "labelled a NEW EVALUATION against changed inputs, not a reproduction")
    fixtures = sub.add_parser("fixtures", help="offline fixture regression (NOT an LLM accuracy measure)")
    fixtures.add_argument("--packs", nargs="+", metavar="NN")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return {"live": cmd_live, "rescore": cmd_rescore, "fixtures": cmd_fixtures}[args.command](args)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
