# make_release_notes.py
# 2026-09-15  Jonghyun Park w/ Claude
#
# 컴포넌트 스코프 태그(<도구>/vX.Y)를 받아 그 도구 스크립트 상단 헤더의
# 해당 버전 changelog 를 뽑아 릴리스 노트 마크다운으로 출력한다.
#
# 묶음(bundle) 자동 감지:
#   태그 커밋에서 두 도구의 버전 파일이 **함께** 추가/이름변경됐으면
#   호환 묶음으로 보고 양쪽 changelog 를 한 노트에 담는다.
#   (extract_data 와 RESHAPE_standard 는 출력 컬럼으로 맞물려 있어,
#    짝이 어긋나면 에러 없이 컬럼이 조용히 유실된다.)
#
# 노트 원천을 **헤더 changelog** 로 고정한 이유:
#   커밋 메시지 기반(gh release create --generate-notes 포함)으로 만들면
#   커밋 제목이 그대로 공개 릴리스 노트로 재발행된다.
#
# 사용:
#   python .github/make_release_notes.py extract_data/v4.5
#   python .github/make_release_notes.py extract_data/v4.5 --title

import glob
import os
import re
import subprocess
import sys

# ════════════ 사용자가 바꿔야 하는 부분 ════════════
COMPONENTS = {
    'extract_data': 'data_extract/extract_data_v*.py',
    'RESHAPE_standard': 'data_extract/RESHAPE_standard_v*.py',
}
# ════════════ 내부 사용 ════════════
AA_HEAD = re.compile(r'^#\s*v(\d+\.\d+)\s*\((\d{4}-\d{2}-\d{2})\):\s*(.*)$')
AA_STRAY = re.compile(r'^#\s*\d{4}-\d{2}-\d{2}:')
VER_IN_NAME = re.compile(r'_v(\d+\.\d+)\.py$')
BULLET1 = ('·', '①', '②', '③', '④', '⑤', '+', '※')
BULLET2 = ('→',)
STRIP_CHARS = ''.join(BULLET1 + BULLET2)

PAIR_WARN = (
    '> ⚠️ **짝이 어긋나도 에러가 나지 않습니다.** 구버전 `RESHAPE_standard` 에 신버전\n'
    '> `extract_data` 의 새 컬럼이 들어오면 passthrough 화이트리스트에 없어서 **조용히 유실**됩니다.\n'
    '> 실행은 되고 데이터만 빠지므로 짝을 맞춰 올리세요.'
)


def newest_file(comp):
    cands = glob.glob(COMPONENTS[comp])
    if not cands:
        sys.exit('ERROR: {} 파일을 찾지 못했습니다'.format(comp))

    def key(p):
        m = VER_IN_NAME.search(os.path.basename(p))
        return [int(x) for x in m.group(1).split('.')] if m else [0, 0]

    return max(cands, key=key)


def parse_header(path):
    """{version: [본문 줄, ...]} — 헤더의 버전 블록"""
    out, cur = {}, None
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line.startswith('#'):
                break
            m = AA_HEAD.match(line)
            if m:
                cur = m.group(1)
                out[cur] = [m.group(3).strip()] if m.group(3).strip() else []
                continue
            if AA_STRAY.match(line):
                cur = None
                continue
            if cur is None:
                continue
            body = line[1:].strip()
            if not body:
                cur = None
                continue
            out[cur].append(body)
    return out


def block_md(lines):
    """헤더 연속줄을 논리 항목으로 재조립 (마커로 시작 = 새 항목)"""
    items = []
    for s in lines:
        s = s.strip()
        if not s:
            continue
        lvl = 2 if s.startswith(BULLET2) else (1 if s.startswith(BULLET1) else 0)
        if lvl and items:
            items.append([lvl, s.lstrip(STRIP_CHARS).strip()])
        elif not items:
            items.append([lvl, s.lstrip(STRIP_CHARS).strip() if lvl else s])
        else:
            items[-1][1] += ' ' + s
    out = []
    for lvl, text in items:
        out.append(text + '\n' if lvl == 0 else ('  ' * (lvl - 1)) + '- ' + text)
    return '\n'.join(out)


def bundle_partner(tag, comp):
    """태그 커밋에서 상대 도구의 버전 파일도 함께 추가됐으면 그 버전을 반환"""
    other = next(c for c in COMPONENTS if c != comp)
    try:
        diff = subprocess.run(
            ['git', 'diff', '--name-status', '--diff-filter=AR', tag + '^', tag],
            capture_output=True, text=True, check=True).stdout
    except Exception:
        return None
    prefix = COMPONENTS[other].replace('*', '')
    for line in diff.splitlines():
        path = line.split('\t')[-1]
        if path.startswith(prefix.rsplit('_v', 1)[0] + '_v') and path.endswith('.py'):
            m = VER_IN_NAME.search(os.path.basename(path))
            if m:
                return other, m.group(1)
    return None


def summarize(lines):
    s = block_md(lines).split('\n')[0]
    s = re.sub(r'^\s*-\s*', '', s)                      # 불릿 마커 제거
    s = re.sub(r'\*\*', '', s).split('—')[0].strip().rstrip('.')
    if len(s) > 60:
        head = s.split(' (')[0].strip()                 # 괄호 설명 먼저 떼보고
        s = head if len(head) >= 15 else s[:57].rstrip() + '...'
    return s


def main():
    if len(sys.argv) < 2:
        sys.exit('사용: make_release_notes.py <도구>/<vX.Y> [--title]')
    tag = sys.argv[1]
    want_title = '--title' in sys.argv[2:]

    if '/' not in tag:
        sys.exit('ERROR: 태그는 <도구>/vX.Y 형식이어야 합니다 (예: extract_data/v4.5)')
    comp, ver = tag.split('/', 1)
    ver = ver[1:] if ver.startswith('v') else ver
    if comp not in COMPONENTS:
        sys.exit('ERROR: 알 수 없는 도구 {!r} — {} 중 하나여야 합니다'.format(
            comp, ', '.join(COMPONENTS)))

    entries = parse_header(newest_file(comp))
    if ver not in entries:
        sys.exit('ERROR: {} 헤더에 v{} 항목이 없습니다. '
                 '버전업 시 헤더 changelog 를 먼저 추가하세요.'.format(comp, ver))

    partner = bundle_partner(tag, comp)
    if want_title:
        head = '{} v{}'.format(comp, ver)
        if partner:
            head += ' + {} v{}'.format(partner[0], partner[1])
        print('{} — {}'.format(head, summarize(entries[ver])))
        return

    if partner:
        pcomp, pver = partner
        pentries = parse_header(newest_file(pcomp))
        print('## {} v{} + {} v{}\n'.format(comp, ver, pcomp, pver))
        print('### 📦 이 릴리스는 묶음입니다\n')
        print('`{}` **v{}** 와 `{}` **v{}** 는 같은 커밋에서 나왔습니다. '
              '**둘 다** 올리세요.\n'.format(comp, ver, pcomp, pver))
        print('| 컴포넌트 | 버전 |\n|---|---|')
        print('| `{}` | **v{}** |'.format(comp, ver))
        print('| `{}` | **v{}** |\n'.format(pcomp, pver))
        print(PAIR_WARN + '\n')
        print('### {} v{} 변경 내역\n'.format(comp, ver))
        print(block_md(entries[ver]))
        if pver in pentries:
            print('\n### {} v{} 변경 내역\n'.format(pcomp, pver))
            print(block_md(pentries[pver]))
    else:
        print('## {} v{}\n'.format(comp, ver))
        print(PAIR_WARN + '\n')
        print('### 변경 내역\n')
        print(block_md(entries[ver]))

    print('\n---')
    print('<sub>노트 원천: 각 스크립트 상단 헤더 changelog</sub>')


if __name__ == '__main__':
    main()
