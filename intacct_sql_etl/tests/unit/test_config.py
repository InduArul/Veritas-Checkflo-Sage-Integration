"""W0 unit tests — config loading/validation + secret resolution + test-target guard (specs/06)."""
import textwrap

import pytest

from intacct_etl import config


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return str(p)


def test_load_example_config_is_valid():
    # the committed template must always be loadable + valid
    cfg = config.load_config("config/config.example.yaml")
    for key in ("run", "environments", "sage", "gl", "ap", "sql", "retry", "log"):
        assert key in cfg


def test_missing_required_section_raises(tmp_path):
    path = _write(tmp_path, """
        run: {}
        environments: [{name: X, sage_creds_key: K}]
        sage: {}
        gl: {}
        ap: {}
        sql: {}
        retry: {}
        # log missing
    """)
    with pytest.raises(config.ConfigError):
        config.load_config(path)


def test_empty_environments_raises(tmp_path):
    path = _write(tmp_path, """
        run: {}
        environments: []
        sage: {}
        gl: {}
        ap: {}
        sql: {}
        retry: {}
        log: {}
    """)
    with pytest.raises(config.ConfigError):
        config.load_config(path)


def test_resolve_secret_from_env():
    env = {"SAGE_PW_EVERGREEN": "s3cr3t"}
    assert config.resolve_secret("SAGE_PW_EVERGREEN", env=env) == "s3cr3t"


def test_resolve_secret_missing_raises():
    with pytest.raises(config.ConfigError):
        config.resolve_secret("NOPE", env={})


def test_assert_test_targets_blocks_prod_names():
    cfg = {
        "sql": {
            "gl_table": "dbo.GLTable",            # PROD name -> must fail in test mode
            "apbill_table": "test_APBillTable",
            "proc_add_gl": "test_AddGLDetailBulk",
        }
    }
    with pytest.raises(config.ConfigError):
        config.assert_test_targets(cfg)


def test_safe_identifier_accepts_qualified_names():
    assert config.safe_identifier("dbo.test_GLTable") == "dbo.test_GLTable"
    assert config.safe_identifier("[Intacct].[dbo].[test_Sync]") == "[Intacct].[dbo].[test_Sync]"


@pytest.mark.parametrize("bad", ["test_GL; DROP TABLE x", "a b", "x'); --", "EXEC(@s)", ""])
def test_safe_identifier_rejects_injection(bad):
    with pytest.raises(config.ConfigError):
        config.safe_identifier(bad)


def test_assert_test_sql_targets_shared_guard_blocks_prod():
    # CR-09: the shared guard used by both config + SqlLoader
    with pytest.raises(config.ConfigError):
        config.assert_test_sql_targets({"gl_table": "dbo.GLTable"})


def test_assert_test_sql_targets_guards_watermark_table():
    # specs/13: the delta watermark control table must also be test_-prefixed in test mode
    with pytest.raises(config.ConfigError):
        config.assert_test_sql_targets({"watermark_table": "dbo.etl_watermark"})
    config.assert_test_sql_targets({"watermark_table": "dbo.test_etl_watermark"})  # ok


def test_assert_test_targets_passes_when_all_prefixed():
    cfg = {
        "sql": {
            "gl_table": "dbo.test_GLTable",
            "apbill_table": "test_APBillTable",
            "proc_add_gl": "test_AddGLDetailBulk",
            "proc_add_ap": "test_AddAPBillBulk",
            "proc_sync_links": "test_SyncAPLinksToGL",
            "proc_update_vendor": "test_UpdateVendorInfoInGL",
        }
    }
    config.assert_test_targets(cfg)  # should not raise
