from modules.brokers import split_take_profit_shares


def test_take_profit_allocations_never_create_zero_quantity_orders():
    assert split_take_profit_shares(1, 2) == [1]
    assert split_take_profit_shares(2, 2) == [1, 1]
    assert split_take_profit_shares(3, 2) == [2, 1]

    for shares in range(1, 25):
        allocations = split_take_profit_shares(shares, 2)
        assert sum(allocations) == shares
        assert all(quantity > 0 for quantity in allocations)
