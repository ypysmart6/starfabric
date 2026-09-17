STARFABRIC_NODE_VERSION = 0.1.0
STARFABRIC_NODE_SITE = $(BR2_EXTERNAL_STARFABRIC_PATH)/../..
STARFABRIC_NODE_SITE_METHOD = local
STARFABRIC_NODE_SUBDIR = onboard
STARFABRIC_NODE_LICENSE = Apache-2.0

define STARFABRIC_NODE_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/target/$(RUSTC_TARGET_NAME)/release/satellite-node-runtime \
		$(TARGET_DIR)/usr/bin/starfabric-node
	$(INSTALL) -D -m 0644 $(STARFABRIC_NODE_SITE)/onboard/packaging/systemd/starfabric-node.service \
		$(TARGET_DIR)/usr/lib/systemd/system/starfabric-node.service
endef

$(eval $(cargo-package))
