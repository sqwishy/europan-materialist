FROM registry.fedoraproject.org/fedora-minimal

RUN microdnf install --setopt=install_weak_deps=False -y dnsmasq \
 && microdnf clean all

ENTRYPOINT ["dnsmasq", "--keep-in-foreground", "--no-hosts", "--no-resolv", "--log-facility=-"]
CMD ["--server=127.0.0.1#5353"]

# vim: syntax=dockerfile
