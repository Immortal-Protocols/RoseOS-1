Synthetic inputs for `scripts/tests/test_factory_package.py` (FLAVOR001). `ab_partitions.txt` and `misc_info.txt`
stand in for a signed target-files' `META/` files; they are made up to exercise each inventory rule (plain images,
a firmware-carried partition `abl`, dynamic partitions in `super_N.img`), not a Frankel or Rango partition list.
The tests build every ZIP, image and flash-script stand-in in memory from these: no real image, key, OTA or
flasher is stored here, and nothing is executed.
