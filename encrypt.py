#!/usr/bin/env python

# Copyright (c) 2012 clowwindy
# Copyright (c) 2013 - 2014 v3aqb
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from collections import defaultdict, deque
import sys
import hashlib
import string
import struct
import logging
from repoze.lru import lru_cache
from M2Crypto.EVP import Cipher
import M2Crypto.Rand
random_string = M2Crypto.Rand.rand_bytes


def get_table(key):
    m = hashlib.md5()
    m.update(key)
    s = m.digest()
    (a, b) = struct.unpack('<QQ', s)
    table = [c for c in string.maketrans('', '')]
    for i in xrange(1, 1024):
        table.sort(lambda x, y: int(a % (ord(x) + i) - a % (ord(y) + i)))
    return table


def check(key, method):
    if method.lower() == 'table':
        encrypt_table = ''.join(get_table(key))
        string.maketrans(encrypt_table, string.maketrans('', ''))
    else:
        try:
            Encryptor(key, method)  # test if the settings if OK
        except Exception as e:
            logging.error(e)
            sys.exit(1)


@lru_cache(128)
def init_table(key):
    encrypt_table = ''.join(get_table(key))
    decrypt_table = string.maketrans(encrypt_table, string.maketrans('', ''))
    return (encrypt_table, decrypt_table)


@lru_cache(128)
def EVP_BytesToKey(password, key_len, iv_len):
    # equivalent to OpenSSL's EVP_BytesToKey() with count 1
    # so that we make the same key and iv as nodejs version
    m = []
    i = 0
    while len(''.join(m)) < key_len:
        md5 = hashlib.md5()
        data = password
        if i > 0:
            data = m[i - 1] + password
        md5.update(data)
        m.append(md5.digest())
        i += 1
    ms = ''.join(m)
    key = ms[:key_len]
    return key


class sized_deque(deque):
    def __init__(self):
        deque.__init__(self, maxlen=1048576)

method_supported = {
    'aes-128-cfb': (16, 16),
    'aes-192-cfb': (24, 16),
    'aes-256-cfb': (32, 16),
    'bf-cfb': (16, 8),
    'camellia-128-cfb': (16, 16),
    'camellia-192-cfb': (24, 16),
    'camellia-256-cfb': (32, 16),
    'cast5-cfb': (16, 8),
    'des-cfb': (8, 8),
    'idea-cfb': (16, 8),
    'rc2-cfb': (16, 8),
    'rc4': (16, 0),
    'seed-cfb': (16, 16),
}

USED_IV = defaultdict(sized_deque)


class Encryptor(object):
    def __init__(self, key, method=None, servermode=False):
        if method == 'table':
            method = None
        self.key = key
        self.method = method
        self.servermode = servermode
        self.iv = None
        self.iv_sent = False
        self.cipher_iv = ''
        self.decipher = None
        if method is not None:
            if servermode:
                self.cipher = self.get_cipher(key, method, 1, random_string(32))
            else:
                while True:
                    iv = random_string(32)
                    if iv not in USED_IV[self.key]:
                        break
                USED_IV[self.key].append(iv)
                self.cipher = self.get_cipher(key, method, 1, iv)
        else:
            self.cipher = None
            self.decipher = 0
            self.encrypt_table, self.decrypt_table = init_table(key)

    def get_cipher_len(self, method):
        method = method.lower()
        m = method_supported.get(method, None)
        return m

    def iv_len(self):
        return len(self.cipher_iv)

    def get_cipher(self, password, method, op, iv):
        password = password.encode('utf-8')
        method = method.lower()
        m = self.get_cipher_len(method)
        if m:
            key = EVP_BytesToKey(password, m[0], m[1])
            if op == 1:
                self.cipher_iv = iv[:m[1]]  # this iv is for cipher, not decipher
            return Cipher(method.replace('-', '_'), key, iv, op, key_as_bytes=0, d='md5', salt=None, i=1, padding=1)

        logging.error('method %s not supported' % method)
        sys.exit(1)

    def encrypt(self, buf):
        if len(buf) == 0:
            return buf
        if self.method is None:
            return string.translate(buf, self.encrypt_table)
        else:
            if self.iv_sent:
                return self.cipher.update(buf)
            else:
                self.iv_sent = True
                return self.cipher_iv + self.cipher.update(buf)

    def decrypt(self, buf):
        if len(buf) == 0:
            return buf
        if self.method is None:
            return string.translate(buf, self.decrypt_table)
        else:
            if self.decipher is None:
                decipher_iv_len = self.get_cipher_len(self.method)[1]
                decipher_iv = buf[:decipher_iv_len]
                if self.servermode:
                    if decipher_iv in USED_IV[self.key]:
                        return 1
                    USED_IV[self.key].append(decipher_iv)
                self.decipher = self.get_cipher(self.key, self.method, 0, decipher_iv)
                buf = buf[decipher_iv_len:]
                if len(buf) == 0:
                    return buf
            return self.decipher.update(buf)
