from __future__ import annotations

from ms8.memory.schema import ledger_schema_path, load_ledger_schema


def test_published_schema_is_draft_2020_12_and_loadable() -> None:
    path = ledger_schema_path()
    schema = load_ledger_schema()

    assert path.is_file()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$ref"] == "#/$defs/ledgerTransaction"


def test_published_schema_defines_all_core_ledger_objects() -> None:
    definitions = load_ledger_schema()["$defs"]

    assert {
        "actor",
        "validTime",
        "memoryEvent",
        "claim",
        "evidence",
        "decision",
        "ledgerEvent",
        "ledgerTransaction",
    } <= set(definitions)
    transaction = definitions["ledgerTransaction"]
    assert transaction["properties"]["schema"]["const"] == "ms8.ledger.v1"
    assert transaction["properties"]["events"]["minItems"] == 1
    assert transaction["additionalProperties"] is False
