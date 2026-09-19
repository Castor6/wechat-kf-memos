import base64
import hashlib
import hmac
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class CallbackCrypto:
    def __init__(self, token: str, encoding_key: str, corp_id: str):
        if len(encoding_key) != 43:
            raise ValueError("EncodingAESKey must be 43 characters")
        self.key = base64.b64decode(encoding_key + "=", validate=True)
        if len(self.key) != 32:
            raise ValueError("Invalid AES key")
        self.token, self.corp_id = token, corp_id

    def decrypt(self, signature: str, timestamp: str, nonce: str, encrypted: str) -> bytes:
        expected = hashlib.sha1(
            "".join(sorted([self.token, timestamp, nonce, encrypted])).encode()
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid callback signature")
        ciphertext = base64.b64decode(encrypted, validate=True)
        decryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.key[:16])).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        if (
            not padded
            or not 1 <= padded[-1] <= 32
            or padded[-padded[-1] :] != bytes([padded[-1]]) * padded[-1]
        ):
            raise ValueError("Invalid callback padding")
        plain = padded[: -padded[-1]]
        if len(plain) < 20:
            raise ValueError("Invalid callback envelope")
        length = struct.unpack("!I", plain[16:20])[0]
        if 20 + length > len(plain) or plain[20 + length :] != self.corp_id.encode():
            raise ValueError("Invalid callback receiver")
        return plain[20 : 20 + length]
