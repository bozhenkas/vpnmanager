"""Тесты логики промоутера RU-кандидатов (scripts/promote_ru_candidates.py)."""
import importlib.util
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "promote_ru_candidates", os.path.join(REPO, "scripts", "promote_ru_candidates.py"))
promote = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(promote)


def test_covered_by_suffix():
    sfx = {"ru", "vk.com", "yandex.net"}
    assert promote.covered_by_suffix("foo.ru", sfx)          # под domain:ru
    assert promote.covered_by_suffix("ru", sfx)
    assert promote.covered_by_suffix("a.b.vk.com", sfx)
    assert not promote.covered_by_suffix("example.com", sfx)
    assert not promote.covered_by_suffix("notvk.com", sfx)   # НЕ суффикс vk.com


def _cand(host, typ, conf, hits, is_ru=True):
    return {"host": host, "type": typ, "hits": hits,
            "verdict": {"is_ru": is_ru, "confidence": conf}}


def test_select_additions_filters_and_dedup():
    suffixes = {"ru"}
    cands = [
        _cand("foo.ru", "domain", 0.95, 10),                 # покрыт domain:ru → skip
        _cand("rutube-cdn.example.com", "domain", 0.92, 12),  # иностранный TLD → add
        _cand("95.213.0.10", "ip", 1.0, 8),                   # IP → add /32
        _cand("low.example.com", "domain", 0.40, 10),         # low conf → skip
        _cand("rare.example.com", "domain", 0.95, 1),         # low hits → skip
        _cand("foreign.example.com", "domain", 0.99, 9, is_ru=False),  # не RU → skip
        _cand("dup.example.com", "domain", 0.9, 9),           # уже в exist → skip
    ]
    add_d, add_ip, skipped = promote.select_additions(
        cands, suffixes,
        exist_domains=["domain:dup.example.com"], exist_ips=[],
        deny=set(), min_conf=0.80, min_hits=5)
    assert add_d == ["domain:rutube-cdn.example.com"]
    assert add_ip == ["95.213.0.10/32"]
    assert skipped == 1  # foo.ru


def test_select_additions_deny():
    add_d, add_ip, _ = promote.select_additions(
        [_cand("blocked.example.com", "domain", 0.99, 99)],
        suffixes=set(), exist_domains=[], exist_ips=[],
        deny={"blocked.example.com"}, min_conf=0.5, min_hits=1)
    assert add_d == [] and add_ip == []


def test_write_and_load_auto_roundtrip(tmp_path, monkeypatch):
    auto = tmp_path / "ru_direct_auto.py"
    monkeypatch.setattr(promote, "AUTO_FILE", str(auto))
    promote.write_auto_file(["domain:a.example.com"], ["1.2.3.4/32"])
    d, ip = promote.load_existing_auto()
    assert d == ["domain:a.example.com"]
    assert ip == ["1.2.3.4/32"]
