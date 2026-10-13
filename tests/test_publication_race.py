"""Execute the real daily commit step against controlled Git outcomes."""
import os
from pathlib import Path
import subprocess
import textwrap

import pytest


def _commit_script():
    root = Path(__file__).resolve().parent.parent
    workflow = (root / ".github/workflows/daily.yml").read_text()
    step = workflow.split("      - name: Commit new events", 1)[1]
    step = step.split("      - name: Job summary", 1)[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


@pytest.mark.parametrize("mode,expected_pushes", [("normal", 1), ("rebase", 2), ("conflict", 1)])
def test_pages_are_only_published_when_the_commit_did_not_need_a_rebase(tmp_path, mode, expected_pushes):
    commands = tmp_path / "commands"
    commands.mkdir()
    git = commands / "git"
    git.write_text('''#!/bin/sh
printf '%s\n' "$*" >> "$GUARD_TEST_DIR/calls"
case "$1" in
  diff) exit 1 ;;
  push)
    count=0
    if [ -f "$GUARD_TEST_DIR/pushes" ]; then count=$(cat "$GUARD_TEST_DIR/pushes"); fi
    count=$((count + 1))
    printf '%s' "$count" > "$GUARD_TEST_DIR/pushes"
    if [ "$GUARD_TEST_MODE" = normal ] || [ "$count" -gt 1 ]; then exit 0; fi
    exit 1 ;;
  rebase)
    if [ "$GUARD_TEST_MODE" = conflict ] && [ "$2" != --abort ]; then exit 1; fi ;;
esac
exit 0
''')
    git.chmod(0o755)
    sleep = commands / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    output = tmp_path / "github-output"
    output.touch()
    env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"],
               GITHUB_OUTPUT=str(output), GUARD_TEST_DIR=str(tmp_path), GUARD_TEST_MODE=mode)
    result = subprocess.run(["bash", "-e", "-c", _commit_script()], cwd=tmp_path,
                            env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert int((tmp_path / "pushes").read_text()) == expected_pushes
    if mode == "normal":
        assert output.read_text() == ""
    else:
        assert "published=false" in output.read_text()
    if mode == "conflict":
        assert "rebase --abort" in (tmp_path / "calls").read_text()
