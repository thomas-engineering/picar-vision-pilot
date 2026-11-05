"""Switch the drive motors off at the PWM chip, whatever state the server was left in.

systemd runs this after the server has ended for any reason (deploy/picar.service, ExecStopPost),
including a crash or kill -9: the PCA9685 keeps its last duty on its own, so without this the car
would keep driving until the restarted server set it to zero. It only needs smbus2, so it still
works when the server cannot start. Run it by hand with: python -m server.motors_off
"""
import sys
import time

from smbus2 import SMBus

from server.hardware import Calibration, Hardware, PCA9685

FULL_OFF = [0, 0, 0, 0x10]  # LEDn_ON_L, ON_H, OFF_L, OFF_H: bit 4 of OFF_H is "full off"
ATTEMPTS = 5


def main():
    cal = Calibration.load()
    channels = (Hardware.MOTOR_PWM_LEFT, Hardware.MOTOR_PWM_RIGHT)
    for attempt in range(ATTEMPTS):
        try:
            with SMBus(cal.i2c_bus) as bus:
                for channel in channels:
                    bus.write_i2c_block_data(cal.pca_address, PCA9685.LED0_ON_L + 4 * channel, FULL_OFF)
                off = all(bus.read_byte_data(cal.pca_address, PCA9685.LED0_ON_L + 4 * channel + 3) & 0x10
                          for channel in channels)
            if off:
                print("motors off")
                return 0
        except OSError as error:  # the I2C bus can be busy or glitch for a moment
            print("attempt %d: %s" % (attempt + 1, error), file=sys.stderr)
        time.sleep(0.05)
    print("could not switch the motors off", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
