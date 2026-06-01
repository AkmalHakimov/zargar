from app.ingestion.telegram_export_parser import normalize_text, parse_messages


def test_parse_telegram_export_messages():
    export = {
        "name": "Education Group",
        "id": "group-1",
        "messages": [
            {"id": 2, "type": "message", "date": "2026-05-20T10:00:00+00:00", "from": "Madina", "text": "Payment confirmed."},
            {"id": 1, "type": "service", "date": "2026-05-20T09:00:00+00:00", "text": "ignored"},
            {"id": 3, "type": "message", "date": "2026-05-20T11:00:00+00:00", "from": "Founder", "text": [{"type": "plain", "text": "Discount"}, " updated"]},
        ],
    }

    messages = parse_messages(export)

    assert [message.message_id for message in messages] == ["2", "3"]
    assert messages[0].chat_title == "Education Group"
    assert messages[1].content == "Discount updated"


def test_normalize_text_handles_telegram_rich_text():
    assert normalize_text(["Hello ", {"type": "bold", "text": "Madina"}]) == "Hello Madina"


def test_parse_text_entities_links_reply_forward_edit_and_media_metadata():
    export = {
        "name": "Ops Group",
        "id": "ops",
        "messages": [
            {
                "id": 10,
                "type": "message",
                "date": "2026-05-20T10:00:00+00:00",
                "edited": "2026-05-20T10:05:00+00:00",
                "from": "Founder",
                "from_id": "user1",
                "reply_to_message_id": 9,
                "forwarded_from": "Partner Chat",
                "text": ["Please review ", {"type": "link", "text": "refund policy", "href": "https://example.com"}],
            },
            {
                "id": 11,
                "type": "message",
                "date": "2026-05-20T11:00:00+00:00",
                "from": "Madina",
                "media_type": "photo",
                "file": "photos/photo_1.jpg",
                "text": "",
            },
            {"id": 12, "type": "service", "date": "2026-05-20T12:00:00+00:00", "text": "Madina joined"},
        ],
    }

    messages = parse_messages(export)

    assert len(messages) == 2
    assert messages[0].content == "Please review refund policy"
    assert messages[0].reply_to_message_id == "9"
    assert messages[0].forwarded_from == "Partner Chat"
    assert messages[0].edited_at is not None
    assert messages[1].content == "[photo: photos/photo_1.jpg]"
    assert messages[1].media_metadata["file"] == "photos/photo_1.jpg"
