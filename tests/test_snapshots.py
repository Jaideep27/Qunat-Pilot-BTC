from quantpilot.snapshots import write, read
from quantpilot import services

def test_ephemeral_host_reads_shared_projections_and_stales_prediction(account):
    observed="2024-01-01T00:00:00+00:00"
    values={
        "candles":services.envelope([{"close":100},{"close":101}],observed),
        "predictions":services.envelope({"action":"LONG","reasons":[],"probability":.9},observed)
    }
    write(account,values)
    assert services.candles(1)["data"] == [{"close":101}]
    result=services.prediction()
    assert result["data"]["action"] == "NO_TRADE"
    assert result["data"]["reasons"] == ["STALE_DATA"]
    assert result["provenance"]["freshness"] == "stale"
    # Serving does not rewrite the persisted projection.
    assert read("predictions")["data"]["action"] == "LONG"

