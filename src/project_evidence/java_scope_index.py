"""Bounded lexical hints from already-observed Java prefixes, never a Java parser."""
import json
import re


def _mask(text):
    # Preserve offsets/newlines, hiding comments and all literal contents.
    out = list(text)
    state = 'code'
    i = 0
    while i < len(text):
        ch = text[i]
        if state == 'code':
            if text.startswith('//', i): state, width = 'line', 2
            elif text.startswith('/*', i): state, width = 'block', 2
            elif text.startswith('"""', i): state, width = 'text', 3
            elif ch in "\"'": state, width = ch, 1
            else:
                i += 1
                continue
        elif state == 'line' and ch == '\n':
            state = 'code'; i += 1; continue
        elif state == 'block' and text.startswith('*/', i):
            state, width = 'code', 2
        elif state == 'text' and text.startswith('"""', i):
            state, width = 'code', 3
        elif state in {'text', '"', "'"} and ch == '\\': width = min(2, len(text)-i)
        elif state in {'"', "'"} and ch == state: state, width = 'code', 1
        else: width = 1
        for pos in range(i, i+width):
            if out[pos] != '\n': out[pos] = ' '
        i += width
    return ''.join(out)


_CONTROL = re.compile(r'^(if|else|try|catch|finally|for|while|do|switch|synchronized)\b')
_METHOD = re.compile(r'^(?:@\w+(?:\([^)]*\))?\s*)*(?:public|private|protected)\s+'
                     r'(?:(?:static|final|synchronized|native|abstract)\s+)*'
                     r'(?:[\w.<>\[\],?]+\s+)+(?P<name>[A-Za-z_$]\w*)\s*\(')


def _scopes(text):
    masked = _mask(text)
    stack, scopes = [], []
    segment, parens = 0, 0
    last_closed = None
    for pos, ch in enumerate(masked):
        if ch == '(': parens += 1
        elif ch == ')': parens = max(0, parens-1)
        elif ch == ';' and not parens: segment = pos+1
        elif ch == '{':
            header = masked[segment:pos]
            start = segment + len(header) - len(header.lstrip())
            header = header.strip()
            control = _CONTROL.match(header)
            method = _METHOD.match(header)
            kind = control.group(1) if control else 'method_candidate' if method else None
            item = None
            if kind:
                item = dict(kind=kind, header_start=masked.count('\n', 0, start)+1,
                            header_end=masked.count('\n', 0, pos)+1, closing_line=None,
                            complete=False, header=' '.join(text[start:pos].split())[:120])
                if kind == 'method_candidate' and len(method.group('name')) <= 80:
                    item['method_name'] = method.group('name')
                if kind in {'catch', 'finally'} and last_closed is not None:
                    previous, end = last_closed
                    # Only a directly adjacent lexical handler chain. An
                    # intervening statement or unmatched brace breaks the link.
                    origin = (previous['header_start'] if previous['kind'] == 'try'
                              else previous.get('try_header_start') if previous['kind'] == 'catch' else None)
                    if origin is not None and not masked[end+1:start].strip():
                        item['try_header_start'] = origin
                scopes.append(item)
            stack.append(item); segment = pos+1
        elif ch == '}':
            last_closed = None
            if stack:
                item = stack.pop()
                if item is not None:
                    item.update(closing_line=masked.count('\n', 0, pos)+1, complete=True)
                    last_closed = (item, pos)
            segment = pos+1
    throws = [masked.count('\n', 0, m.start())+1 for m in re.finditer(r'\bthrow\b', masked)]
    return scopes, throws


def scope_index(observations, *, max_chars=2800):
    """No filesystem, inferred calls, selected citations, or semantic assertions.

    Require a contiguous observed prefix from line1 for literal/comment state.
    Partial prefixes explicitly mark open braces; fragments across gaps are not
    guessed. Candidate omission never proves a declaration or scope is absent.
    """
    groups = {}
    for observation in observations:
        value = observation['result']
        if (observation['name'] != 'read_excerpt' or value.get('status') != 'ok'
                or value.get('truncated') is not False or value.get('last_line_truncated') is True
                or not value.get('path', '').endswith('.java')):
            continue
        lines = value['excerpt'].split('\n')
        mapping = groups.setdefault(value['path'], {})
        for line, text in enumerate(lines, value['start_line']):
            if line in mapping and mapping[line] != text:
                raise ValueError('Conflicting observed prefix')
            mapping[line] = text
    result = dict(status='lexical_candidates_only', semantic_verified=False, truncated=False, files=[])
    for path, mapping in sorted(groups.items()):
        end = 0
        while end+1 in mapping: end += 1
        item = dict(path=path, observed_prefix_end=end, status='prefix_unavailable', scopes=[], throw_lines=[])
        if end:
            text = '\n'.join(mapping[line] for line in range(1, end+1))
            if '\\u' in text:
                item['status'] = 'unicode_escape_not_indexed'
            else:
                item['status'] = 'observed_prefix_only'
                item['scopes'], item['throw_lines'] = _scopes(text)
        result['files'].append(item)
    cap = min(2800, max_chars)
    if len(json.dumps(result, ensure_ascii=False)) > cap:
        # Raw observations already carry the declaration text. Retain observed
        # boundaries and open-prefix state before spending the cap on snippets.
        # This optional navigation projection never selects answer citations.
        result['truncated'] = True
        for file in result['files']:
            for scope in file['scopes']:
                scope.pop('header', None)
    if len(json.dumps(result, ensure_ascii=False)) > cap:
        # New navigation metadata must not displace existing boundaries or an
        # observed open-prefix marker. Omission never means absence in source.
        for file in result['files']:
            for scope in file['scopes']:
                scope.pop('method_name', None)
                scope.pop('try_header_start', None)
    while len(json.dumps(result, ensure_ascii=False)) > cap:
        result['truncated'] = True
        if not result['files']: return None
        last = result['files'][-1]
        if last['scopes']: last['scopes'].pop()
        elif last['throw_lines']: last['throw_lines'].pop()
        else: result['files'].pop()
    return result
