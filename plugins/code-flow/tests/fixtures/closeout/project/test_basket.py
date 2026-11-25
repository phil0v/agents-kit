import unittest

from basket import basket_total


class BasketTests(unittest.TestCase):
    def test_adds_prices(self):
        self.assertEqual(basket_total([4, 7, 3]), 14)
