"""分散処理基盤(orchestration)の決定論ロジックを検証する。

3台の変換ワーカーを「常に最新DNで回す」ための版整合・準備判定を固定する。
ssh/ビルド等のI/Oではなく、決定を担う純関数を網羅する。
"""

import importlib

ew = importlib.import_module("scripts.orchestration.ensure_worker")


class TestParseVersions:
    def test_parses_two_shas(self):
        assert ew.parse_versions("abc123def456 789ghi012jkl") == (
            "abc123def456",
            "789ghi012jkl",
        )

    def test_truncates_to_12(self):
        local, remote = ew.parse_versions("a" * 40 + " " + "b" * 40)
        assert local == "a" * 12 and remote == "b" * 12

    def test_missing_returns_empty(self):
        assert ew.parse_versions("") == ("", "")
        assert ew.parse_versions("onlyone") == ("", "")


class TestDecideAction:
    def test_stale_triggers_update_and_rebuild(self):
        a = ew.decide_action("aaa", "bbb", binary_exists=True)
        assert a["update"] is True and a["rebuild"] is True

    def test_up_to_date_with_binary_no_action(self):
        a = ew.decide_action("aaa", "aaa", binary_exists=True)
        assert a["update"] is False and a["rebuild"] is False

    def test_up_to_date_missing_binary_rebuilds(self):
        a = ew.decide_action("aaa", "aaa", binary_exists=False)
        assert a["update"] is False and a["rebuild"] is True

    def test_version_fetch_failure_no_action(self):
        a = ew.decide_action("", "", binary_exists=True)
        assert a["update"] is False and a["rebuild"] is False
        assert "取得失敗" in a["reason"]


class TestIsReady:
    def test_ready_when_all_present(self):
        assert ew.is_ready({"binary": True, "venv": True, "device_ok": True}) is True

    def test_not_ready_missing_device(self):
        assert ew.is_ready({"binary": True, "venv": True, "device_ok": False}) is False

    def test_not_ready_missing_binary(self):
        assert ew.is_ready({"binary": False, "venv": True, "device_ok": True}) is False


class TestDeviceCheckCmd:
    def test_cuda_uses_cuda_available(self):
        assert "torch.cuda.is_available()" in ew._device_check_cmd("cuda")

    def test_mps_uses_mps_available(self):
        assert "torch.backends.mps.is_available()" in ew._device_check_cmd("mps")


def test_worker_config_has_three_machines():
    names = {w.name for w in ew.WORKERS}
    assert names == {"studio", "mini", "asus"}
    asus = next(w for w in ew.WORKERS if w.name == "asus")
    assert asus.device == "cuda"
    assert asus.ocr_batch == "small"  # VRAM 4GB 配慮
