SUMMARY = "StarFabric autonomous satellite node runtime"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://LICENSE;md5=dummy-replace-for-release"
inherit cargo systemd
SRC_URI = "file://onboard file://starfabric-node.service"
S = "${WORKDIR}/onboard"
SYSTEMD_SERVICE:${PN} = "starfabric-node.service"
do_install:append() {
    install -d ${D}${bindir} ${D}${systemd_system_unitdir}
    install -m 0755 ${B}/target/${RUST_HOST_SYS}/release/satellite-node-runtime ${D}${bindir}/starfabric-node
    install -m 0644 ${WORKDIR}/starfabric-node.service ${D}${systemd_system_unitdir}/
}
