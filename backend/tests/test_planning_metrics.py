from app.planning.metrics import walking_totals


def test_walking_totals_include_public_transport_access_and_transfers():
    stages = [
        {
            "mode": "transit",
            "duration_minutes": 42,
            "distance_km": 8.5,
            "transit_legs": [
                {"mode": "walk", "duration_minutes": 7, "distance_km": 0.55},
                {"mode": "subway", "duration_minutes": 18, "distance_km": 7.2},
                {"mode": "walk", "duration_minutes": 5, "distance_km": 0.36},
            ],
        },
        {
            "mode": "walking",
            "duration_minutes": 20,
            "distance_km": 1.3,
            "transit_legs": [],
        },
    ]

    assert walking_totals(stages) == (32, 2.21)
