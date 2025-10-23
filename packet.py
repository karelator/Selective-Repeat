class Packet:
    """Represent a packet of data.
    Note - DO NOT REMOVE or CHANGE the data attribute!
    The simulation assumes this is present!"""

    def __init__(self, binary_data, seq_num=0, ack_num=0, is_ack=False):
        self.data = binary_data
        self.seq_num = seq_num
        self.ack_num = ack_num
        self.is_ack = is_ack
