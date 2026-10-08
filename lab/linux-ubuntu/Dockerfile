FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-server systemd iproute2 net-tools iputils-ping traceroute \
        tcpdump curl dnsutils netcat-openbsd ethtool procps psmisc \
        vim-tiny less sudo && \
    rm -rf /var/lib/apt/lists/* && \
    echo 'root:ntbxlab' | chpasswd && \
    sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config && \
    ln -sf /usr/lib/systemd/system/ssh.service /etc/systemd/system/multi-user.target.wants/ssh.service

EXPOSE 22

# systemd as PID 1 (compose sets command /sbin/init);
# ssh.service is enabled above so systemd starts sshd on boot.
CMD ["/sbin/init"]
