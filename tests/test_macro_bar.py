"""宏观条：腾讯 s_ 简版指数行情解析。"""
from quantcore.quant.macro_bar import parse_index_payload

SAMPLE = (
    'v_s_sh000001="1~上证指数~000001~3391.88~10.14~0.30~319129749~416024730~~~1";\n'
    'v_s_sz399001="51~深证成指~399001~10318.36~-25.31~-0.24~412345678~523456789~~~2";\n'
)


def test_parse_index_payload():
    rows = parse_index_payload(SAMPLE)
    assert len(rows) == 2
    sh = rows[0]
    assert sh["code"] == "sh000001"
    assert sh["name"] == "上证指数"
    assert sh["price"] == 3391.88
    assert sh["change"] == 10.14
    assert sh["change_percent"] == 0.30
    assert rows[1]["change_percent"] == -0.24


def test_parse_index_payload_garbage_returns_empty():
    assert parse_index_payload("") == []
    assert parse_index_payload('v_s_sh000001="broken";') == []
