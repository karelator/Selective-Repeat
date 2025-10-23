from copy import copy
from threading import Timer

from packet import Packet


from copy import copy
from threading import Timer

from packet import Packet


def checksum16(b: bytes) -> int:
    """Enkel 16-bit sjekksum (sum av bytes mod 65535)."""
    total = 0
    # summer 2 og 2 bytes for å få litt bedre blanding
    for i in range(0, len(b), 2):
        word = b[i] << 8
        if i + 1 < len(b):
            word |= b[i + 1]
        total = (total + word) & 0xFFFF
    return total


class TransportLayer:
    """Selective Repeat med sjekksum, vindu og retransmisjon ved timeout."""

    def __init__(self):
        # --- tidsstyring ---
        self.timer = None
        self.timeout = 0.4  # sekunder

        # --- SR (sender) ---
        self.window_size = 8
        self.base = 0                 # laveste ikke-ACK’ede sekvens
        self.next_seq_num = 0         # neste sekvens å sende
        self.sent_packets = {}        # seq -> Packet (u-ACK’et)

        # --- SR (mottaker) ---
        self.rcv_base = 0             # laveste ikke-leverte sekvens
        self.recv_window = {}         # seq -> bytes (buffer av gyldige, out-of-order)

    def with_logger(self, logger):
        self.logger = logger
        return self

    def register_above(self, layer):
        self.application_layer = layer

    def register_below(self, layer):
        self.network_layer = layer

    # ---------------------- SENDER ----------------------

    def from_app(self, binary_data):
        # Ikke send hvis vinduet er fullt
        if self.next_seq_num >= self.base + self.window_size:
            # Queue unsent data for later ticks
            self.application_layer.payload.pos -= len(binary_data)
            return


        # Lag datapakke med sekvens og sjekksum
        pkt = Packet(binary_data, seq_num=self.next_seq_num, is_ack=False)
        pkt.checksum = checksum16(pkt.data)

        # Buffer pakken for ev. retransmisjon og send
        self.sent_packets[pkt.seq_num] = pkt
        self.logger.info(f"Sending packet {pkt.seq_num}")
        self.network_layer.send(copy(pkt))

        # Start/forny timer hvis vi nettopp sendte 'base'
        if self.base == self.next_seq_num:
            self.reset_timer(self._on_timeout)

        # Flytt neste sekvens
        self.next_seq_num += 1

    # ---------------------- MOTTAR FRA NETT ----------------------

    def from_network(self, packet: Packet):
        # ACK-håndtering
        if packet.is_ack:
            ack = packet.ack_num
            self.logger.info(f"Received ACK {ack}")
            if ack in self.sent_packets:
                del self.sent_packets[ack]

                # Flytt base frem til første u-ACK’ede
                while self.base not in self.sent_packets and self.base < self.next_seq_num:
                    self.base += 1

            # Timerstyring
            if not self.sent_packets:
                # alt ACK’et -> stopp timer
                if self.timer and self.timer.is_alive():
                    self.timer.cancel()
                self.logger.info("All packets acknowledged, stopping timer.")
                print("Finished!")
            else:
                # fortsatt u-ACK’ede -> hold timeren i gang
                self.reset_timer(self._on_timeout)
            return

        # Datapakke-håndtering (Selective Repeat på mottaker)
        seq = packet.seq_num
        
        # 1) Verifiser sjekksum. Korrupte pakker droppes (ingen ACK).
        expected = getattr(packet, "checksum", None)
        if expected is None or expected != checksum16(packet.data):
            # Korrupsjon => la senderen time ut og retransmittere
            self.logger.warning(f"Dropping corrupted packet {seq}")
            return

        # 2) Sjekk om seq er innenfor mottakervinduet
        if not (self.rcv_base <= seq < self.rcv_base + self.window_size):
            # Utenfor vindu: send duplikat-ACK for siste leverte (her: ACK’er den gyldige som kom,
            # eller ignorerer hvis bak vinduet). For enkelhet: ACK’er bare hvis i/lavere enn vindu.
            if seq < self.rcv_base:
                ack = Packet(b"", ack_num=seq, is_ack=True)
                self.network_layer.send(copy(ack))
            return

        # 3) Buffer gyldig pakke og ACK den spesifikke sekvensen
        if seq not in self.recv_window:
            self.recv_window[seq] = packet.data

        ack = Packet(b"", ack_num=seq, is_ack=True)
        self.network_layer.send(copy(ack))

        # 4) Lever i rekkefølge fra rcv_base så langt vi kan
        while self.rcv_base in self.recv_window:
            data = self.recv_window.pop(self.rcv_base)
            self.application_layer.receive_from_transport(data)
            self.rcv_base += 1

    # ---------------------- TIMEOUT ----------------------

    def _on_timeout(self):
        if not self.sent_packets:
            return
        self.logger.warning("Timeout – resending all unACKed packets")
        for seq, pkt in list(self.sent_packets.items()):
            self.network_layer.send(copy(pkt))
        self.reset_timer(self._on_timeout)


    # ---------------------- TIMER-HJELPER ----------------------

    def reset_timer(self, callback, *args):
        # Stopp gammel timer før ny
        if self.timer and self.timer.is_alive():
            self.timer.cancel()
        self.timer = Timer(self.timeout, callback, args=args)
        self.timer.start()
