FROM centos:7

# CentOS 7 is EOL (2024-06): official mirrorlist is gone, switch to vault.
# sed recipe from the official CentOS EOL FAQ.
RUN sed -i 's/mirror.centos.org/vault.centos.org/g; s/^#.*baseurl=http/baseurl=http/g; s/^mirrorlist=http/#mirrorlist=http/g' /etc/yum.repos.d/CentOS-*.repo && \
    yum install -y openssh-server systemd iproute net-tools iputils tcpdump \
        curl bind-utils nmap-ncat ethtool psmisc vim-enhanced less sudo && \
    yum clean all && \
    ssh-keygen -A && \
    echo 'root:ntbxlab' | chpasswd && \
    sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config && \
    mkdir -p /etc/systemd/system/sshd.service.d && \
    printf '[Service]\nType=simple\nExecStart=\nExecStart=/usr/sbin/sshd -D\n' \
        > /etc/systemd/system/sshd.service.d/foreground.conf && \
    ln -sf /usr/lib/systemd/system/sshd.service /etc/systemd/system/multi-user.target.wants/sshd.service

EXPOSE 22

# systemd as PID 1 (compose sets command /sbin/init);
# sshd.service is enabled above so systemd starts sshd on boot.
CMD ["/sbin/init"]
