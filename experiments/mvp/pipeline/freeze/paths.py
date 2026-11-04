"""Path checks shared by freeze writers and launch verification."""
from pathlib import Path


def reject_symlinks(path: str | Path, root: str | Path, *, checked=None) -> Path:
    """Check the named path and every ancestor inside root before opening it."""
    root = Path(root).absolute()
    path = Path(path).absolute()
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError(f'path outside allowed root: {path}') from None
    if '..' in relative.parts:
        raise ValueError(f'noncanonical path: {path}')
    current = root
    if (checked is None or current not in checked) and current.is_symlink():
        raise ValueError(f'symlink excluded: {current}')
    if checked is not None:
        checked.add(current)
    for part in relative.parts:
        current /= part
        if checked is not None and current in checked and current != path:
            continue
        if current.is_symlink():
            raise ValueError(f'symlink excluded: {current}')
        if checked is not None and current != path:
            checked.add(current)
    return path


def contained_output(path: str | Path, root: str | Path) -> Path:
    """Resolve an output, including existing ancestors, inside its write fence."""
    if Path(root).is_symlink():
        raise ValueError('output fence must not be a symlink')
    root = Path(root).resolve()
    path = Path(path)
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(f'output outside freeze directory: {path}') from None
    if resolved == root:
        raise ValueError('output must name a file or subdirectory inside freeze')
    return resolved


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('paths', type=Path, nargs='+')
    args = parser.parse_args()
    try:
        outputs = [contained_output(path, args.root) for path in args.paths]
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print('\n'.join(str(path) for path in outputs))


if __name__ == '__main__':
    main()
