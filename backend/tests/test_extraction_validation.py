from app.memory.entity_extractor import validate_entities
from app.memory.fact_extractor import validate_facts


def test_validate_entities_filters_empty_names():
    result = validate_entities({"entities": [{"name": "Madina", "type": "employee"}, {"name": "", "type": "policy"}]})

    assert result == [{"name": "Madina", "type": "employee", "summary": ""}]


def test_validate_facts_normalizes_relation_type():
    result = validate_facts(
        {
            "facts": [
                {
                    "source_entity": "Founder",
                    "relation_type": "approved",
                    "target_entity": "Discount Policy",
                    "fact_text": "Founder approved the discount policy.",
                    "confidence": 0.91,
                }
            ]
        }
    )

    assert result[0]["relation_type"] == "APPROVED"
    assert result[0]["confidence"] == 0.91

