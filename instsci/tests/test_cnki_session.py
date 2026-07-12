from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from instsci.cnki_session import (
    classify_cnki_session,
    cnki_verification_visible,
    load_cnki_batch,
    safe_page_url,
)
from instsci.config import Config
from instsci.browser_doctor import _POWERSHELL_PROBE


class CnkiSessionTests(TestCase):
    def test_cnki_verification_page_requires_user(self) -> None:
        self.assertEqual(
            classify_cnki_session("https://kns.cnki.net/verify/home?captchaType=clickWord", "安全验证"),
            "human_verification_required",
        )

    def test_cnki_page_is_session_ready(self) -> None:
        self.assertEqual(
            classify_cnki_session("https://kns.cnki.net/kcms2/article/abstract?v=token", "文章详情"),
            "session_ready",
        )

    def test_report_url_drops_query_tokens(self) -> None:
        self.assertEqual(
            safe_page_url("https://kns.cnki.net/verify/home?captchaId=secret#fragment"),
            "https://kns.cnki.net/verify/home",
        )

    def test_config_uses_dedicated_cnki_profile(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg = Config(
                output_dir=str(Path(tmp) / "papers"),
                cache_dir=str(Path(tmp) / "cache"),
                cookie_path=str(Path(tmp) / "cookies.json"),
                chrome_profile_dir=str(Path(tmp) / "chrome"),
                cnki_profile_dir=str(Path(tmp) / "cnki"),
                carsi_cookie_dir=str(Path(tmp) / "carsi"),
            )
            cfg.ensure_dirs()
            self.assertTrue(Path(cfg.cnki_profile_dir).is_dir())
            self.assertNotEqual(cfg.cnki_profile_dir, cfg.chrome_profile_dir)

    def test_browser_doctor_does_not_treat_captcha_id_as_active_challenge(self) -> None:
        probe = _POWERSHELL_PROBE
        self.assertIn('[?&]captchaType=', probe)
        self.assertNotIn('$joined -match "confirm you are human', probe)
        self.assertIn('PDF下载', probe)

    def test_load_cnki_batch_validates_records(self) -> None:
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "batch.json"
            source.write_text(
                '[{"record_id":"YKDZ202305004","title":"测试题名","url":"https://kns.cnki.net/KCMS/detail/detail.aspx?filename=YKDZ202305004","zotero_item_key":"JGN5J75A"}]',
                encoding="utf-8",
            )
            rows = load_cnki_batch(source)
            self.assertEqual(rows[0]["record_id"], "YKDZ202305004")
            self.assertEqual(rows[0]["zotero_item_key"], "JGN5J75A")

    def test_load_cnki_batch_rejects_unsafe_record_id(self) -> None:
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "batch.json"
            source.write_text(
                '[{"record_id":"../escape","title":"测试题名","url":"https://kns.cnki.net/article"}]',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unsafe record_id"):
                load_cnki_batch(source)

    def test_hidden_cnki_captcha_markup_does_not_pause_batch(self) -> None:
        class Locator:
            def count(self): return 1
            def nth(self, _index): return self
            def is_visible(self): return False

        class Page:
            url = "https://kns.cnki.net/kcms2/article/abstract?captchaId=retained"
            def title(self): return "文章详情"
            def get_by_text(self, _marker, exact=False): return Locator()

        self.assertFalse(cnki_verification_visible(Page()))

    def test_visible_cnki_captcha_pauses_batch(self) -> None:
        class Locator:
            def count(self): return 1
            def nth(self, _index): return self
            def is_visible(self): return True

        class Page:
            url = "https://kns.cnki.net/kcms2/article/abstract"
            def title(self): return "文章详情"
            def get_by_text(self, _marker, exact=False): return Locator()

        self.assertTrue(cnki_verification_visible(Page()))
