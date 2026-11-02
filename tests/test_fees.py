import pytest

from desk.fees import fomo_solana_fee


def test_published_solana_bands_and_top_band_discount():
    assert fomo_solana_fee(0) == 0
    assert fomo_solana_fee(4, 0.10) == pytest.approx(0.10)
    assert fomo_solana_fee(10, 0.10) == pytest.approx(0.20)
    assert fomo_solana_fee(100, 0.10) == pytest.approx(0.95)
    assert fomo_solana_fee(189.99, 0.10) == pytest.approx(0.95)
    assert fomo_solana_fee(190, 0.10) == pytest.approx(190 * 0.005 * 0.90)
    assert fomo_solana_fee(200, 0.0) == pytest.approx(1.0)
    assert fomo_solana_fee(200, 0.10) == pytest.approx(0.90)
    # The discount does not rewrite the flat bands.
    assert fomo_solana_fee(100, 0.0) == fomo_solana_fee(100, 0.10)
