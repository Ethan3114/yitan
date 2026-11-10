"""整理成章：出处必须能在原话里找到。"""

from app.services.grounding import elder_text, excerpt_around, keep_grounded, transcripts_disagree
from app.services.memoir import choose_memoir, reader_fact
from app.services.tingwu import format_turns


def test_prefers_side_transcript():
    text = elder_text({"side": "旁路这一句", "realtime_user": "实时这一句"})
    assert text == "旁路这一句"


def test_full_transcript_is_the_writing_source():
    text = elder_text({"full": "全量这一句", "side": "旁路这一句", "realtime_user": "实时这一句"})
    assert text == "全量这一句"


def test_tingwu_turns_keep_speaker_and_time():
    text = format_turns(
        [
            {"SpeakerId": "1", "Words": [{"Start": 65000, "Text": "五十一岁"}]},
            {"SpeakerId": "2", "Words": [{"Start": 67000, "Text": "三十六"}]},
        ]
    )
    assert "[01:05 发言人1] 五十一岁" in text
    assert "[01:07 发言人2] 三十六" in text


def test_quote_must_appear_in_source():
    source = "天不亮就跟着我爹下田"
    assert keep_grounded("天不亮就下田。", "天不亮就跟着我爹下田", source)
    assert not keep_grounded("他后来当了老师。", "当了老师", source)


def test_quote_must_sit_inside_one_turn():
    source = "[00:01 发言人1] 我四岁妈妈不在了。\n[00:20 发言人2] 后来去了兰州。"
    assert keep_grounded("我四岁就没有了妈妈。", "我四岁妈妈不在了", source)
    assert not keep_grounded("后来我去了兰州。", "我四岁妈妈不在了。后来去了兰州", source)


def test_excerpt_stays_near_the_quote():
    source = "[00:01 发言人1] 我四岁妈妈不在了。\n[00:20 发言人2] 后来去了兰州。\n[02:00 发言人1] 这句太晚了。"
    text = excerpt_around(source, "我四岁妈妈不在了")
    assert "我四岁妈妈不在了" in text
    assert "后来去了兰州" in text
    assert "这句太晚了" not in text


def test_disagreement_when_tracks_differ():
    assert transcripts_disagree({"side": "甲", "realtime_user": "乙"})
    assert not transcripts_disagree({"side": "", "realtime_user": "乙"})


def test_machine_review_blocks_unread_text():
    source = "天不亮就跟着我爹下田"
    assert choose_memoir(source, False, [("天还不亮，我就跟着我爹下田。", source)], []) == []


def test_machine_review_keeps_grounded_prose():
    source = "天不亮就跟着我爹下田"
    kept = choose_memoir(source, True, [("天还不亮，我就跟着我爹下田。", source)], [])
    assert kept == [("天还不亮，我就跟着我爹下田。", source)]


def test_review_drops_invented_line_and_uses_writer():
    source = "天不亮就跟着我爹下田"
    kept = choose_memoir(
        source,
        True,
        [("后来他当了老师。", "当了老师")],
        [("天还不亮，我就跟着我爹下田。", source)],
    )
    assert kept == [("天还不亮，我就跟着我爹下田。", source)]


def test_reader_fact_uses_plain_words():
    topic, detail = reader_fact("两路转写不一致", "豆包听写和写书用的转写不一样")
    assert "豆包" not in topic
    assert "豆包" not in detail
    assert "听写有出入" == topic


def test_draft_requires_writing_key(client):
    login_id = client.post("/api/v1/auth/login", json={"code": "yitan-local"})
    assert login_id.status_code == 200
    profile_id = client.post("/api/v1/profiles", json={"elder_name": "王爷爷"}).json()["id"]
    from app.models import Call, Transcript
    from app.services.store import new_id

    db = client.app.state.db_factory()
    owner = client.get("/api/v1/me").json()["owner_id"]
    call = Call(id=new_id(), owner_id=owner, profile_id=profile_id, status="ready")
    db.add(call)
    db.add(Transcript(id=new_id(), owner_id=owner, call_id=call.id, track="side", text="天不亮就跟着我爹下田"))
    db.commit()
    db.close()
    response = client.post(f"/api/v1/calls/{call.id}/draft")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "model_not_configured"
