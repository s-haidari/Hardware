"""cubemx — build the STM32 pin database from CubeMX MCU XML, from scratch.

parse.py  : CubeMX XML -> McuData (pins, signals)
classify.py: a pin -> electrical class, canonical name, and roles
builder.py: iterate the XML set -> sqlite (mcu, mcu_package_pin, pin_function, pin_role)
"""
