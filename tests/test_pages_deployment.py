"""Exercise the actual composite steps with controlled deployment outcomes.

Only the external official action is stubbed; execute the committed shell
confirmation, output export, and waits (through a recording sleep command).
"""
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
ACTION = ROOT / ".github/actions/deploy-site/action.yml"
URL = "https://srinivasledger.github.io/filing-signals/"


def _expression(value, steps, cancelled):
    expression = value.strip().removeprefix("${{").removesuffix("}}").strip()
    expression = expression.replace("cancelled()", repr(cancelled))

    def lookup(match):
        record = steps.get(match[1], {})
        key = match[2]
        value = (record.get("outputs", {}).get(key.split(".")[1], "")
                 if key.startswith("outputs.") else record.get(key, ""))
        return repr(value)

    expression = re.sub(r"steps\.(\w+)\.(outcome|outputs\.\w+)", lookup, expression)
    expression = expression.replace("&&", " and ").replace("||", " or ").replace("!", "not ")
    return eval(expression, {"__builtins__": {}}, {})


def _run_action(tmp_path, deployments):
    action = yaml.safe_load(ACTION.read_text())
    commands = tmp_path / "bin"
    commands.mkdir()
    delays = tmp_path / "delays"
    sleep = commands / "sleep"
    sleep.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$TEST_DELAYS"\n')
    sleep.chmod(0o755)
    state = {}
    cancelled = failed = False
    calls = 0
    for step in action["runs"]["steps"]:
        name = step["id"]
        if not _expression(step["if"], state, cancelled):
            state[name] = {"outcome": "skipped", "outputs": {}}
            continue
        if "uses" in step:
            assert step["uses"] == "actions/deploy-pages@v5"
            outcome, page_url = deployments[calls]
            calls += 1
            cancelled = outcome == "cancelled"
            state[name] = {"outcome": outcome, "outputs": {"page_url": page_url}}
            if outcome == "failure" and not step.get("continue-on-error"):
                failed = True
        else:
            output = tmp_path / (name + ".outputs")
            env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"],
                       TEST_DELAYS=str(delays), GITHUB_OUTPUT=str(output))
            env.update({key: str(_expression(value, state, cancelled))
                        for key, value in step.get("env", {}).items()})
            result = subprocess.run(["bash", "-e", "-c", step["run"]], env=env,
                                    capture_output=True, text=True, timeout=5)
            outputs = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
            state[name] = {"outcome": "success" if result.returncode == 0 else "failure",
                           "outputs": outputs}
            failed |= result.returncode != 0
    page_url = _expression(action["outputs"]["page_url"]["value"], state, cancelled)
    return ("cancelled" if cancelled else "failure" if failed else "success",
            calls, delays.read_text().splitlines() if delays.exists() else [], page_url)


@pytest.mark.parametrize("failures,expected_delays", [(0, []), (1, ["15"]), (2, ["15", "45"])])
def test_temporary_artifact_lookup_failure_recovers(tmp_path, failures, expected_delays):
    attempts = [("failure", "https://failed-attempt.invalid/")] * failures + [("success", URL)]
    status, calls, delays, page_url = _run_action(tmp_path, attempts)
    assert (status, calls, delays, page_url) == ("success", failures + 1, expected_delays, URL)


def test_persistent_failure_stays_failed_without_exporting_a_url(tmp_path):
    status, calls, delays, page_url = _run_action(tmp_path, [("failure", URL)] * 3)
    assert (status, calls, delays, page_url) == ("failure", 3, ["15", "45"], "")


def test_cancellation_does_not_retry_or_report_success(tmp_path):
    assert _run_action(tmp_path, [("cancelled", URL)]) == ("cancelled", 1, [], "")


@pytest.mark.parametrize("page_url", ["", " \t"])
def test_missing_success_url_is_a_failure(tmp_path, page_url):
    assert _run_action(tmp_path, [("success", page_url)]) == ("failure", 1, [], "")


@pytest.mark.parametrize("filename", ["daily.yml", "publish.yml"])
def test_both_publication_paths_use_the_guard_and_keep_live_checks(filename):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
    steps = next(iter(workflow["jobs"].values()))["steps"]
    deployment = next(s for s in steps if s.get("id") == "deployment")
    assert deployment["uses"] == "./.github/actions/deploy-site"
    assert not deployment.get("continue-on-error")
    upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-pages-artifact@"))
    live = next(s for s in steps if s.get("id") == "verify_live")
    assert steps.index(upload) < steps.index(deployment) < steps.index(live)
    assert live["env"]["BASE_URL"] == "${{ steps.deployment.outputs.page_url }}"
    assert live["env"]["VERIFY_DEPLOYED_BUILD"] == "1"
    if filename == "daily.yml":
        assert deployment["if"] == "steps.commit.outputs.published != 'false'"
