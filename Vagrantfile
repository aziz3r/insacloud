# -*- mode: ruby -*-
# vi: set ft=ruby :
# =============================================================================
#  Vagrantfile - VM de "production" locale d'InsaCloud (multi-provider)
# -----------------------------------------------------------------------------
#  Cible : Ubuntu 22.04, 2 vCPU / 4 Go, joignable sur 192.168.56.10.
#
#  Fournisseurs, par ordre de préférence (Vagrant prend le premier utilisable) :
#    1. vmware_desktop  Mac Apple Silicon / Intel, Windows, Linux
#                       -> vagrant plugin install vagrant-vmware-desktop  (+ Vagrant VMware Utility)
#    2. parallels       Mac Apple Silicon / Intel
#                       -> vagrant plugin install vagrant-parallels
#    3. qemu            Mac Apple Silicon sans hyperviseur commercial
#                       -> brew install qemu && vagrant plugin install vagrant-qemu
#    4. virtualbox      PC Windows / Linux, Mac Intel (repli)
#
#  Forcer un fournisseur : vagrant up --provider=vmware_desktop
#                          (ou export VAGRANT_DEFAULT_PROVIDER=parallels)
#
#  Les boxes "bento/ubuntu-22.04" existent en amd64 ET arm64 pour VMware,
#  Parallels et VirtualBox ; QEMU utilise une box dédiée arm64/amd64.
# =============================================================================

VM_IP     = "192.168.56.10"
VM_NAME   = "insacloud"
VM_MEMORY = 4096          # les machines "bureau" consomment 1 Go chacune
VM_CPUS   = 2

# Architecture du poste : arm64 (Apple Silicon) ou amd64.
# Double détection : le Ruby embarqué par Vagrant peut tourner sous Rosetta.
HOST_ARM64 = (RUBY_PLATFORM =~ /arm64|aarch64/) ||
             (`uname -m 2>/dev/null`.strip =~ /arm64|aarch64/ rescue false) ? true : false

Vagrant.configure("2") do |config|
  config.vm.box      = "bento/ubuntu-22.04"
  config.vm.hostname = VM_NAME

  # Réseau privé hôte <-> VM : le site sera sur http://192.168.56.10:5000
  config.vm.network "private_network", ip: VM_IP

  # Pas de dossier partagé : Ansible copie lui-même le code (déploiement réel)
  config.vm.synced_folder ".", "/vagrant", disabled: true

  # ---- 1. VMware Fusion / Workstation --------------------------------------
  config.vm.provider "vmware_desktop" do |v, override|
    v.vmx["displayName"] = VM_NAME
    v.vmx["memsize"]     = VM_MEMORY.to_s
    v.vmx["numvcpus"]    = VM_CPUS.to_s
    v.gui = false
    # Fusion crée son propre réseau privé ; l'IP statique reste 192.168.56.10
    v.allowlist_verified = true
  end

  # ---- 2. Parallels Desktop ------------------------------------------------
  config.vm.provider "parallels" do |prl, override|
    prl.name   = VM_NAME
    prl.memory = VM_MEMORY
    prl.cpus   = VM_CPUS
    prl.update_guest_tools = false
  end

  # ---- 3. QEMU (Apple Silicon) ---------------------------------------------
  # vagrant-qemu ne gère pas les réseaux privés à IP fixe : on redirige les
  # ports sur localhost. Pour Ansible, utilisez alors :
  #   vagrant ssh-config   -> ansible_host=127.0.0.1 ansible_port=<port>
  # et le site est sur http://localhost:5000.
  config.vm.provider "qemu" do |q, override|
    override.vm.box = HOST_ARM64 ? "perk/ubuntu-2204-arm64" : "generic/ubuntu2204"
    override.vm.network "private_network", ip: VM_IP, disabled: true
    override.vm.network "forwarded_port", guest: 5000, host: 5000
    (8000..9000).step(100) { |p| override.vm.network "forwarded_port", guest: p, host: p, auto_correct: true }
    q.memory = "#{VM_MEMORY}M"
    q.smp    = VM_CPUS
    q.arch   = HOST_ARM64 ? "aarch64" : "x86_64"
    q.machine = HOST_ARM64 ? "virt,accel=hvf,highmem=on" : "q35,accel=hvf"
    q.cpu    = "host"
  end

  # ---- 4. VirtualBox (repli PC Windows / Linux, Mac Intel) -----------------
  config.vm.provider "virtualbox" do |vb|
    vb.name   = VM_NAME
    vb.memory = VM_MEMORY
    vb.cpus   = VM_CPUS
    vb.gui    = false
  end

  # ---- Pré-requis Ansible : python3 sur la cible (idempotent) ---------------
  config.vm.provision "shell", name: "bootstrap-python", inline: <<-SHELL
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-apt > /dev/null
  SHELL

  # ---- Provisionnement Ansible (optionnel : décommentez si Ansible est installé)
  # config.vm.provision "ansible" do |ansible|
  #   ansible.playbook           = "3_ansible/site.yml"
  #   ansible.inventory_path     = "3_ansible/inventaire.ini"
  #   ansible.ask_vault_pass     = true
  #   ansible.compatibility_mode = "2.0"
  # end
end
