import msgspec

from .codecs import ACLMessage, Codec, MangoMessage, Performatives, enum_serializer


class FastJSON(Codec):
    def __init__(self):
        super().__init__()
        self.add_serializer(*ACLMessage.__json_serializer__())
        self.add_serializer(*MangoMessage.__json_serializer__())
        self.add_serializer(*enum_serializer(Performatives))

        self.encoder = msgspec.json.Encoder(enc_hook=self.serialize_obj)
        self.decoder = msgspec.json.Decoder(
            dec_hook=lambda _, b: self.deserialize_obj(b), type=MangoMessage
        )

    def encode(self, data):
        return self.encoder.encode(data)

    def decode(self, data):
        return self.decoder.decode(data)
