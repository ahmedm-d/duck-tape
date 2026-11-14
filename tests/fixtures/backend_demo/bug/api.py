from config import DEFAULT_QUANTITY
from pricing import quote_total


def quote(payload):
    quantity = payload.get('quantity', DEFAULT_QUANTITY)
    return {'quantity': quantity, 'total': quote_total(quantity)}
