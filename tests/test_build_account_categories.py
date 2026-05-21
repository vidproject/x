from scripts.build_account_categories import classify


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
