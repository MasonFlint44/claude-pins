"""Free checks on the plugin files: frontmatter, the commands' dynamic-context snippets, and the
install skill's shell steps, run in a sandbox with CLAUDE_PLUGIN_ROOT pointing at this checkout.

The paid, headless end-to-end runs of the skills live in tests/skills/run.sh (run by hand)."""
import json
import os
import re
import subprocess
from pathlib import Path

from tests.helpers import REPO, Sandbox

SID = "11111111-1111-1111-1111-111111111111"


def frontmatter(path: Path) -> dict:
    text = path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, f"{path} has no frontmatter"
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"')
    return out


def bash_blocks(path: Path) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", path.read_text(), re.S)


class PluginFileTests(Sandbox):
    def test_manifest(self):
        m = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(m["name"], "pins")
        from claude_pins import __version__
        self.assertEqual(m["version"], __version__)
        self.assertTrue((REPO / m["commands"]).is_dir())
        self.assertTrue((REPO / m["skills"]).is_dir())
        changelog = (REPO / "CHANGELOG.md").read_text()
        self.assertIn(f"## {m['version']} — ", changelog, "CHANGELOG lacks a section for the plugin version")

    def test_commands_frontmatter(self):
        for name in ("pin", "unpin"):
            fm = frontmatter(REPO / "commands" / f"{name}.md")
            self.assertTrue(fm.get("description"))
            self.assertIn("Bash(${CLAUDE_PLUGIN_ROOT}/bin/pin:*)", fm["allowed-tools"])
            body = (REPO / "commands" / f"{name}.md").read_text()
            self.assertIn("${CLAUDE_SESSION_ID}", body)
            self.assertNotIn("CLAUDE_SESSION_ID}", body.replace("${CLAUDE_SESSION_ID}", ""))  # no misspelt variants

    def test_skills_frontmatter(self):
        for d in (REPO / "skills").iterdir():
            fm = frontmatter(d / "SKILL.md")
            self.assertEqual(fm["name"], d.name)
            self.assertTrue(len(fm["description"]) > 40, f"{d.name}: description too thin to trigger on")

    def snippet(self, name: str) -> str:
        body = (REPO / "commands" / f"{name}.md").read_text()
        m = re.search(r"!`(.*?)`", body)
        assert m, "no dynamic-context snippet"
        return m.group(1)

    def run_snippet(self, cmd: str) -> str:
        env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(REPO), "CLAUDE_SESSION_ID": SID}
        return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env, check=True).stdout.strip()

    def test_command_snippets_report_status(self):
        for name in ("pin", "unpin"):
            self.assertEqual(self.run_snippet(self.snippet(name)), "unpinned")
        self.make_session(SID, title="Standup prep")
        self.run_snippet(f'"${{CLAUDE_PLUGIN_ROOT}}/bin/pin" add "${{CLAUDE_SESSION_ID}}" standup-prep --title "Standup prep"')
        for name in ("pin", "unpin"):
            self.assertEqual(self.run_snippet(self.snippet(name)), "pinned\tstandup-prep\tStandup prep")
        # the documented unpin command, verbatim from the command file
        body = (REPO / "commands" / "unpin.md").read_text()
        m = re.search(r"```\n\s*(\"\$\{CLAUDE_PLUGIN_ROOT\}/bin/pin\" unpin <alias>)\n", body)
        self.assertIsNotNone(m)
        out = self.run_snippet(m.group(1).replace("<alias>", "standup-prep"))
        self.assertIn("✓ unpinned standup-prep · pin undo", out)

    def test_install_skill_steps(self):
        blocks = bash_blocks(REPO / "skills" / "install" / "SKILL.md")
        self.assertGreaterEqual(len(blocks), 1)
        env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(REPO)}
        for block in blocks:
            subprocess.run(["bash", "-eu", "-c", block], capture_output=True, text=True, env=env, check=True)
        link = self.home / ".local" / "bin" / "pin"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.path.realpath(link), str(REPO / "bin" / "pin"))
        out = subprocess.run([str(link), "--version"], capture_output=True, text=True, env=env).stdout
        self.assertIn("pin ", out)
        # a real file in the way is left alone
        link.unlink(); link.write_text("#!/bin/sh\necho mine\n")
        r = subprocess.run(["bash", "-eu", "-c", blocks[0]], capture_output=True, text=True, env=env)
        self.assertIn("not a symlink", r.stdout)
        self.assertEqual(link.read_text(), "#!/bin/sh\necho mine\n")

    def test_doctor_skill_table_matches_doctor_output(self):
        """Every doctor line the skill explains is a line pin doctor can actually print."""
        from claude_pins import cli, cost, fzf, store
        src = "".join(Path(m.__file__).read_text() for m in (cli, cost, fzf, store))
        skill = (REPO / "skills" / "doctor" / "SKILL.md").read_text()
        for phrase in ("fzf: not found", "need ≥ 0.44", "ccusage: not installed", "offline table has no price for",
                       "even online", "online fallback unreachable", "corrupt", "not found (set CLAUDE_CONFIG_DIR?)",
                       "cleanupPeriodDays"):
            self.assertIn(phrase, skill, f"skill does not explain {phrase!r}")
            self.assertIn(phrase.split(" (")[0], src, f"doctor never prints {phrase[:30]!r}")


class ReadmeTests(Sandbox):
    def test_key_table_matches_defaults(self):
        """The README's Keys table lists exactly the bound defaults from keymap.ACTIONS."""
        from claude_pins.keymap import ACTIONS
        readme = (REPO / "README.md").read_text()
        section = readme.split("## Keys", 1)[1].split("## Command line", 1)[0]
        documented = set()
        for line in section.splitlines():
            if not line.startswith("|") or "---" in line or "| key |" in line:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            documented |= {cells[i] for i in (1, 4) if i < len(cells) and cells[i]}
        defaults = {a.key for a in ACTIONS if a.key}
        self.assertEqual(documented, defaults)
        for a in ACTIONS:
            if not a.key:
                self.assertIn(a.label.lower().replace("toggle ", ""), section.lower(), f"{a.id}: palette-only action not mentioned")
        for name in [a.id for a in ACTIONS]:
            self.assertIn(f"`{name}`", section, f"keys.toml action name {name} not documented")

    def test_cli_reference_lists_every_subcommand(self):
        from claude_pins.cli import SUBCOMMANDS
        readme = (REPO / "README.md").read_text()
        section = readme.split("## Command line", 1)[1].split("## Files", 1)[0]
        for sub in SUBCOMMANDS:
            if sub.startswith("_") or sub in ("help", "ls"):
                continue
            self.assertIn(f"pin {sub}", section, f"subcommand {sub} missing from the README")

    def test_env_knobs_documented(self):
        src = "".join(p.read_text() for p in (REPO / "claude_pins").glob("*.py"))
        knobs = set(re.findall(r"CLAUDE_PINS_[A-Z_]+", src)) - {"CLAUDE_PINS_EXE"}  # EXE is internal plumbing
        readme = (REPO / "README.md").read_text()
        for k in sorted(knobs):
            self.assertIn(k, readme, f"{k} undocumented")
