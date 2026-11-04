include vendor/adevtool/config/mk/google_devices/common/BoardConfig-common.mk

BOARD_SYSTEM_KERNEL_MODULES_BLOCKLIST_FILE := $(KERNEL_MODULE_DIR)/system_dlkm.modules.blocklist

# Kernel modules that are listed in vendor_kernel_boot.modules.load
BOARD_VENDOR_KERNEL_RAMDISK_KERNEL_MODULES_LOAD_FILE := $(strip $(shell cat $(KERNEL_MODULE_DIR)/vendor_kernel_boot.modules.load))
ifndef BOARD_VENDOR_KERNEL_RAMDISK_KERNEL_MODULES_LOAD_FILE
$(error vendor_kernel_boot.modules.load not found or empty)
endif
BOARD_VENDOR_KERNEL_RAMDISK_KERNEL_MODULES_LOAD := $(BOARD_VENDOR_KERNEL_RAMDISK_KERNEL_MODULES_LOAD_EXTRA)
BOARD_VENDOR_KERNEL_RAMDISK_KERNEL_MODULES_LOAD += $(BOARD_VENDOR_KERNEL_RAMDISK_KERNEL_MODULES_LOAD_FILE)
BOARD_VENDOR_KERNEL_RAMDISK_KERNEL_MODULES := $(addprefix $(KERNEL_MODULE_DIR)/, $(BOARD_VENDOR_KERNEL_RAMDISK_KERNEL_MODULES_LOAD_EXTRA))
BOARD_VENDOR_KERNEL_RAMDISK_KERNEL_MODULES += $(addprefix $(KERNEL_MODULE_DIR)/, $(notdir $(BOARD_VENDOR_KERNEL_RAMDISK_KERNEL_MODULES_LOAD_FILE)))

BOARD_VENDOR_KERNEL_MODULES_LOAD := $(strip $(shell cat $(KERNEL_MODULE_DIR)/vendor_dlkm.modules.load))
#$(warning BOARD_VENDOR_KERNEL_MODULES_LOAD $(BOARD_VENDOR_KERNEL_MODULES_LOAD))
ifndef BOARD_VENDOR_KERNEL_MODULES_LOAD
$(error vendor_dlkm.modules.load not found or empty)
endif

#ifneq ($(BOARD_SYSTEM_KERNEL_MODULES),)
#BOARD_VENDOR_KERNEL_MODULES := $(addprefix $(KERNEL_MODULE_DIR)/, $(notdir $(BOARD_VENDOR_KERNEL_MODULES_LOAD)))
#else
# todo not on zuma/zumapro?
#BOARD_VENDOR_KERNEL_MODULES := $(KERNEL_MODULES)
#endif

BOARD_VENDOR_KERNEL_MODULES := $(addprefix $(KERNEL_MODULE_DIR)/, $(notdir $(BOARD_VENDOR_KERNEL_MODULES_LOAD)))

BOARD_SYSTEM_KERNEL_MODULES_LOAD := $(strip $(shell cat $(KERNEL_MODULE_DIR)/system_dlkm.modules.load))
BOARD_SYSTEM_KERNEL_MODULES := $(addprefix $(KERNEL_MODULE_DIR)/, $(notdir $(BOARD_SYSTEM_KERNEL_MODULES_LOAD)))

PRODUCT_PRIVATE_SEPOLICY_DIRS += vendor/adevtool/config/mk/google_devices/common/sepolicy/product/private
PRODUCT_PUBLIC_SEPOLICY_DIRS += vendor/adevtool/config/mk/google_devices/common/sepolicy/product/public
