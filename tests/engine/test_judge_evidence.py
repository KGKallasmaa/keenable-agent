from searchaudit.judge import cited_first


def test_cited_pages_go_first_and_order_is_otherwise_kept():
    pieces = ["search a https://x.org", "search b https://y.org", "fetch https://cited.gov/order"]
    assert cited_first(pieces, ["https://cited.gov/order"]) == [pieces[2], pieces[0], pieces[1]]
    assert cited_first(pieces, []) == pieces


def test_grade_rule_over_checklist_marks():
    from searchaudit.judge import grade_from

    checklist = {"facts": [{"id": 1, "level": "core"}, {"id": 2, "level": "required"}, {"id": 3, "level": "required"}]}
    ok = [{"id": 1, "status": "correct"}, {"id": 2, "status": "correct"}, {"id": 3, "status": "correct"}]
    assert grade_from(checklist, ok, []) == ("c", 1.0)
    detail_missing = [{"id": 1, "status": "correct"}, {"id": 2, "status": "missing"}, {"id": 3, "status": "correct"}]
    assert grade_from(checklist, detail_missing, [])[0] == "p"
    core_wrong = [{"id": 1, "status": "contradicted"}, {"id": 2, "status": "correct"}, {"id": 3, "status": "correct"}]
    assert grade_from(checklist, core_wrong, [])[0] == "w"
    assert grade_from(checklist, ok, [{"id": 1, "asserted": True}])[0] == "w"
    assert grade_from(checklist, ok[:2], [])[0] == "p"  # an unmarked fact counts as missing


def test_context_facts_only_count_when_contradicted():
    from searchaudit.judge import grade_from

    checklist = {"facts": [{"id": 1, "level": "core"}, {"id": 2, "level": "context"}]}
    assert grade_from(checklist, [{"id": 1, "status": "correct"}, {"id": 2, "status": "missing"}], []) == ("c", 1.0)
    assert grade_from(checklist, [{"id": 1, "status": "correct"}, {"id": 2, "status": "contradicted"}], [])[0] == "p"
