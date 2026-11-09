"""Check page continuity before projecting bounded events. Raw pages stay durable."""
from pathlib import Path
from guard.models import canonical_json, sha256_hex


def assemble(pages, *, archive=None, max_events=4096):
    snapshot, cursor, processes, gaps, refs = None, {}, {}, set(), []
    container_start = None
    count = 0
    done = False
    def observed():
        iterator = iter(pages)
        while True:
            try:
                yield next(iterator)
            except StopIteration:
                return
            except Exception:
                gaps.add('CALLBACK_PAGE_READ_FAILED')
                return
    for index, page in enumerate(observed()):
        try:
            if (index >= 256 or done or type(page) is not dict or page.get('schema') != 'guard-callback-page/2'
                    or type(page.get('snapshot')) is not dict or type(page.get('rows')) is not list
                    or type(page.get('cursor')) is not dict or type(page.get('gaps')) is not list
                    or type(page.get('done')) is not bool):
                raise ValueError('CALLBACK_PAGE_INVALID')
            if snapshot is None:
                snapshot = page['snapshot']
                cursor = dict(page.get('start_cursor', {}))
                if any(name not in snapshot or type(offset) is not int or not 0 <= offset <= snapshot[name]['size'] for name, offset in cursor.items()):
                    raise ValueError('CALLBACK_START_CURSOR_INVALID')
            if snapshot != page['snapshot']:
                raise ValueError('CALLBACK_SNAPSHOT_CHANGED')
            next_cursor = dict(cursor)
            # Validate the whole page before committing any of its rows.
            for row in page['rows']:
                name, begin, end = row['file'], row['offset'], row['end']
                if (name not in snapshot or type(begin) is not int or begin != next_cursor.get(name, 0)
                        or type(end) is not int or not begin < end <= snapshot[name]['size']):
                    raise ValueError('CALLBACK_PAGE_DISCONTINUITY')
                next_cursor[name] = end
            if not page['gaps'] and any(page['cursor'].get(k, 0) != next_cursor.get(k, 0) for k in snapshot):
                raise ValueError('CALLBACK_PAGE_DISCONTINUITY')
        except (ValueError, KeyError, TypeError):
            gaps.add('CALLBACK_PAGE_INVALID')
            break
        raw = canonical_json(page)
        if archive is not None:
            path = Path(archive) / f'page-{index:04d}.json'
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(raw)
                refs.append({'path': path.name, 'sha256': sha256_hex(raw)})
            except FileExistsError:
                raise  # Never replace or reuse an existing capture directory.
            except OSError:
                gaps.add('CALLBACK_PAGE_ARCHIVE_FAILED')
        gaps.update(page['gaps'])
        if index == 0:
            container_start = page.get('container_start_monotonic_ns')
        for row in page['rows']:
            name = row['file']
            identity = snapshot[name]['pid'], snapshot[name]['start_ticks']
            process = processes.setdefault(identity, {'pid': identity[0], 'start_ticks': identity[1], 'events': []})
            if count < max_events:
                process['events'].append(row['event'])
            else:
                gaps.add('CALLBACK_PROJECTION_LIMIT')
            count += 1
        cursor = page['cursor']
        done = page['done'] is True
    if not done or snapshot is None or any(cursor.get(k, 0) != item['size'] for k, item in snapshot.items()):
        gaps.add('CALLBACK_PREFIX_INCOMPLETE')
    return {'processes': list(processes.values()), 'truncated': bool(gaps), 'diagnostic_codes': sorted(gaps),
            'page_refs': refs, 'source_events': count, 'next_cursor': cursor, 'container_start_monotonic_ns': container_start,
            'scope': 'checkpointed trial range; legacy prefixes require explicit finalization'}
