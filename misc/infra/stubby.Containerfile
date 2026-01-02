FROM registry.fedoraproject.org/fedora-minimal

RUN microdnf install --setopt=install_weak_deps=False -y stubby \
 && microdnf clean all

RUN cat << EOF > /etc/stubby/stubby.yml
listen_addresses:
- 127.0.0.1@5353
upstream_recursive_servers:
- address_data: 94.140.14.140
  tls_auth_name: "unfiltered.adguard-dns.com"
- address_data: 94.140.14.141
  tls_auth_name: "unfiltered.adguard-dns.com"
EOF

# ENTRYPOINT ["stubby"]

# vim: syntax=dockerfile
