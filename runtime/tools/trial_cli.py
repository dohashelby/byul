"""Shared argparse registration for trial tools; option semantics stay explicit."""


def add_options(parser, definitions):
    for flags, options in definitions:
        parser.add_argument(*flags, **options)
    return parser
