"""The schema-1 Proposer migration is retired; its original evidence is immutable."""


def rerender():
    raise ValueError('Retired cache is read-only historical evidence. Use schema-2 prefetch on bound inputs; '
                     'templates never carry recorded model labels.')


if __name__ == '__main__':
    rerender()
