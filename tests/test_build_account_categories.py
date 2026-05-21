from scripts.build_account_categories import build, classify, service_badges


def test_retired_us_marshal_personal_bio_is_not_government() -> None:
    assert (
        classify(
            "TheRealRazielah",
            {
                "display_name": "Lawrence Moore",
                "description": (
                    "DEPUTY US MARSHAL RET. /1811 CRIMINAL INVESTIGATOR / "
                    "U.S. ARMY SAPPER LEADER"
                ),
                "verified_type": None,
            },
        )
        is None
    )


def test_government_verified_accounts_still_classify_as_government() -> None:
    assert classify(
        "USMarshalsHQ",
        {
            "display_name": "U.S. Marshals Service",
            "description": "Official account of the U.S. Marshals Service.",
            "verified_type": "Government",
        },
    )["category"] == "government"


def test_service_history_gets_badges_not_government_category() -> None:
    user = {
        "display_name": "Lawrence Moore",
        "description": (
            "DEPUTY US MARSHAL RET. /1811 CRIMINAL INVESTIGATOR / "
            "U.S. ARMY SAPPER LEADER"
        ),
        "verified_type": None,
    }

    assert service_badges(user) == ["veteran", "retired-police"]


def test_build_keeps_badge_only_accounts_public(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.build_account_categories.load_config_accounts",
        lambda: {},
    )
    monkeypatch.setattr(
        "scripts.build_account_categories.load_users",
        lambda: {
            "TheRealRazielah": {
                "display_name": "Lawrence Moore",
                "description": "DEPUTY US MARSHAL RET. / U.S. ARMY SAPPER LEADER",
                "verified_type": None,
            }
        },
    )
    monkeypatch.setattr(
        "scripts.build_account_categories.observed_handles",
        lambda: ({"TheRealRazielah"}, {"TheRealRazielah": 1}),
    )

    meta = build()["categories"]["TheRealRazielah"]
    assert meta["category"] == "public"
    assert meta["badges"] == ["veteran", "retired-police"]
