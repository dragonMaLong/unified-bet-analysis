"""The update script keeps translated messages and paths separate from code."""

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from tristar_bet import i18n, updater


MESSAGE_KEYS = {
    "SuccessMessage": "更新成功！立即重启软件",
    "UpdateTitle": "软件更新",
    "FailureMessage": "自动更新失败，请手动运行已下载的更新文件：",
    "FailureTitle": "软件更新失败",
}

# These are deliberately never created or executed. Apostrophes, dollar signs,
# and non-ASCII text are valid in Windows paths and must survive unchanged.
PATHS = {
    "OldPath": Path("C:/Users/O'Brien/中文 {folder}/BET $(not-a-command).exe"),
    "NewPath": Path("C:/Users/O'Brien/下载目录/BET 1.3.8.exe"),
    "BackupPath": Path("C:/Users/O'Brien/中文 {folder}/BET $(not-a-command).exe.old"),
    "LogPath": Path("C:/Users/O'Brien/下载目录/apply_update.log"),
}


def make_script():
    return updater._replacement_script_text(*PATHS.values(), current_pid=12345)


def parse_script(tmp_path, script):
    """Ask the real PowerShell parser for AST data; never run the update script."""
    powershell = shutil.which(updater._powershell_executable())
    if not powershell:
        pytest.skip("PowerShell is required to check generated script syntax")
    target = tmp_path / "generated_update.ps1"
    target.write_bytes(script.encode("utf-8-sig"))
    probe = tmp_path / "parse_only.ps1"
    probe.write_text(
        r'''param([string]$TargetPath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $TargetPath, [ref]$tokens, [ref]$parseErrors)
$values = @{}
$names = @('OldPath', 'NewPath', 'BackupPath', 'LogPath',
           'SuccessMessage', 'UpdateTitle', 'FailureMessage', 'FailureTitle')
foreach ($name in $names) {
    $assignment = $ast.EndBlock.Statements | Where-Object {
        $_ -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $_.Left.VariablePath.UserPath -eq $name
    }
    if ($assignment) {
        $expression = $assignment.Right.Expression
        $values[$name] = @{
            type = $expression.GetType().Name
            value = $expression.Value
        }
    }
}
$commands = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true) | ForEach-Object { $_.GetCommandName() })
@{
    errors = @($parseErrors | ForEach-Object { $_.Message })
    values = $values
    commands = $commands
} | ConvertTo-Json -Depth 5 -Compress
''',
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", str(probe), str(target)],
        capture_output=True, encoding="utf-8-sig", timeout=30, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_only_messages_are_sent_to_translator(monkeypatch, language):
    monkeypatch.setattr(i18n, "_current", language)
    translate = Mock(wraps=i18n.tr)
    monkeypatch.setattr(updater, "tr", translate)

    script = make_script()

    assert [call.args for call in translate.call_args_list] == [
        (key,) for key in MESSAGE_KEYS.values()
    ]
    for key in MESSAGE_KEYS.values():
        text = i18n.tr(key)
        assert text in script
        if language == "en":
            assert text != key, f"Missing updater message translation: {key}"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_script_parses_in_both_languages_with_quoted_paths(tmp_path, monkeypatch, language):
    monkeypatch.setattr(i18n, "_current", language)
    parsed = parse_script(tmp_path, make_script())

    assert parsed["errors"] == []
    expected = {name: str(path) for name, path in PATHS.items()}
    expected.update({name: i18n.tr(key) for name, key in MESSAGE_KEYS.items()})
    assert parsed["values"] == {
        name: {"type": "StringConstantExpressionAst", "value": value}
        for name, value in expected.items()
    }
    # A successful replacement still resets the inherited PyInstaller state
    # before starting the installed executable, after the confirmation dialog.
    commands = parsed["commands"]
    assert "Set-PyInstallerRestartEnvironment" in commands
    assert commands.index("Copy-Item") < commands.index("Show-UpdateMessage")
    assert commands.index("Show-UpdateMessage") < commands.index("Start-UpdatedExecutable")


def test_translation_with_shell_syntax_remains_literal_data(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    baseline = parse_script(tmp_path, make_script())
    messages = {
        key: f"{key}\n' ; Write-Error 'injected' ; # $(Get-Item .) `\" {{0}}"
        for key in MESSAGE_KEYS.values()
    }
    monkeypatch.setattr(updater, "tr", messages.__getitem__)

    parsed = parse_script(tmp_path, make_script())

    assert parsed["errors"] == []
    assert parsed["commands"] == baseline["commands"]
    for name, key in MESSAGE_KEYS.items():
        assert parsed["values"][name] == {
            "type": "StringConstantExpressionAst", "value": messages[key]
        }
