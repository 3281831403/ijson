'''
Pure-python parsing backend.
'''
from __future__ import unicode_literals
import decimal
import re
from codecs import getreader
from json.decoder import scanstring

from ijson import common
from ijson.compat import bytetype


BUFSIZE = 16 * 1024
LEXEME_RE = re.compile(r'[a-z0-9eE\.\+-]+|\S')
# Same number grammar as the one used by the stdlib json module: no leading
# zeroes, no leading '+', no trailing dot or empty exponent...
NUMBER_RE = re.compile(
    r'(-?(?:0|[1-9]\d*))(\.\d+)?([eE][-+]?\d+)?\Z'
)
# Maximum nesting depth of arrays and objects. Deeper structures are rejected
# with a JSONError instead of blowing the Python recursion limit.
JSON_MAX_DEPTH = 100

# Proper prefixes of the literal keywords. A lexeme cut off at EOF that is one
# of these means truncated data rather than garbage input.
_LITERAL_PREFIXES = {
    word[:i]
    for word in ('null', 'true', 'false')
    for i in range(1, len(word))
}


class UnexpectedSymbol(common.JSONError):
    def __init__(self, symbol, pos):
        super(UnexpectedSymbol, self).__init__(
            'Unexpected symbol %r at %d' % (symbol, pos)
        )


def Lexer(f, buf_size=BUFSIZE):
    if type(f.read(0)) == bytetype:
        f = getreader('utf-8')(f)

    def read():
        # A malformed UTF-8 stream is a parse error, not an arbitrary codec
        # exception. Truncated multibyte sequences at EOF surface below as
        # incomplete string lexemes.
        try:
            return f.read(buf_size)
        except UnicodeDecodeError as e:
            raise common.JSONError(e)

    buf = read()
    pos = 0
    discarded = 0
    while True:
        match = LEXEME_RE.search(buf, pos)
        if match:
            lexeme = match.group()
            if lexeme == '"':
                pos = match.start()
                start = pos + 1
                while True:
                    try:
                        end = buf.index('"', start)
                        escpos = end - 1
                        while buf[escpos] == '\\':
                            escpos -= 1
                        if (end - escpos) % 2 == 0:
                            start = end + 1
                        else:
                            break
                    except ValueError:
                        data = read()
                        if not data:
                            raise common.IncompleteJSONError('Incomplete string lexeme')
                        buf += data
                yield discarded + pos, buf[pos:end + 1]
                pos = end + 1
            else:
                while match.end() == len(buf):
                    data = read()
                    if not data:
                        break
                    buf += data
                    match = LEXEME_RE.search(buf, pos)
                    lexeme = match.group()
                yield discarded + match.start(), lexeme
                pos = match.end()
        else:
            data = read()
            if not data:
                break
            discarded += len(buf)
            buf = data
            pos = 0


def parse_value(lexer, symbol=None, pos=0, depth=0):
    try:
        if symbol is None:
            pos, symbol = next(lexer)
        if symbol == 'null':
            yield ('null', None)
        elif symbol == 'true':
            yield ('boolean', True)
        elif symbol == 'false':
            yield ('boolean', False)
        elif symbol == '[':
            for event in parse_array(lexer, depth + 1):
                yield event
        elif symbol == '{':
            for event in parse_object(lexer, depth + 1):
                yield event
        elif symbol[0] == '"':
            try:
                yield ('string', parse_string(symbol))
            except ValueError as e:
                # Bad escapes and bare control characters are parse errors.
                raise common.JSONError(e)
        else:
            if symbol in _LITERAL_PREFIXES:
                # A cut-off beginning of null/true/false: the stream ends
                # before the keyword is complete.
                raise common.IncompleteJSONError('Incomplete JSON data')
            if not NUMBER_RE.match(symbol):
                raise UnexpectedSymbol(symbol, pos)
            try:
                yield ('number', common.number(symbol))
            except decimal.InvalidOperation:
                raise UnexpectedSymbol(symbol, pos)
    except StopIteration:
        # PEP 479: a generator must not let StopIteration escape. Reading
        # past the end while a value is expected means incomplete data.
        raise common.IncompleteJSONError('Incomplete JSON data')


def parse_string(symbol):
    return scanstring(symbol, 1)[0]


def parse_array(lexer, depth=1):
    if depth > JSON_MAX_DEPTH:
        raise common.JSONError(
            'JSON nesting limit %d exceeded' % JSON_MAX_DEPTH
        )
    yield ('start_array', None)
    try:
        pos, symbol = next(lexer)
        if symbol != ']':
            while True:
                for event in parse_value(lexer, symbol, pos, depth):
                    yield event
                pos, symbol = next(lexer)
                if symbol == ']':
                    break
                if symbol != ',':
                    raise UnexpectedSymbol(symbol, pos)
                pos, symbol = next(lexer)
        yield ('end_array', None)
    except StopIteration:
        raise common.IncompleteJSONError('Incomplete JSON data')


def parse_object(lexer, depth=1):
    if depth > JSON_MAX_DEPTH:
        raise common.JSONError(
            'JSON nesting limit %d exceeded' % JSON_MAX_DEPTH
        )
    yield ('start_map', None)
    try:
        pos, symbol = next(lexer)
        if symbol != '}':
            while True:
                if symbol[0] != '"':
                    raise UnexpectedSymbol(symbol, pos)
                try:
                    yield ('map_key', parse_string(symbol))
                except ValueError as e:
                    # Bad escapes and bare control characters are parse errors.
                    raise common.JSONError(e)
                pos, symbol = next(lexer)
                if symbol != ':':
                    raise UnexpectedSymbol(symbol, pos)
                for event in parse_value(lexer, None, pos, depth):
                    yield event
                pos, symbol = next(lexer)
                if symbol == '}':
                    break
                if symbol != ',':
                    raise UnexpectedSymbol(symbol, pos)
                pos, symbol = next(lexer)
        yield ('end_map', None)
    except StopIteration:
        raise common.IncompleteJSONError('Incomplete JSON data')


def basic_parse(file, buf_size=BUFSIZE):
    '''
    Iterator yielding unprefixed events.

    Parameters:

    - file: a readable file-like object with JSON input
    '''
    lexer = iter(Lexer(file, buf_size))
    for value in parse_value(lexer):
        yield value
    try:
        next(lexer)
    except StopIteration:
        pass
    else:
        raise common.JSONError('Additional data')


def parse(file, buf_size=BUFSIZE):
    '''
    Backend-specific wrapper for ijson.common.parse.
    '''
    return common.parse(basic_parse(file, buf_size=buf_size))


def items(file, prefix):
    '''
    Backend-specific wrapper for ijson.common.items.
    '''
    return common.items(parse(file), prefix)
