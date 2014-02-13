#!/usr/bin/env python

import deploy_utils
import service_config
import subprocess
import sys
import time

from log import Log

ALL_JOBS = ["journalnode", "zkfc", "namenode", "datanode"]

SHELL_COMMAND_INFO = {
  "dfs": ("org.apache.hadoop.fs.FsShell",
      "run a filesystem command on the file systems supported in Hadoop"),
  "dfsadmin": ("org.apache.hadoop.hdfs.tools.DFSAdmin",
      "run a DFS admin client"),
  "haadmin": ("org.apache.hadoop.hdfs.tools.DFSHAAdmin",
      "run a DFS HA admin client"),
  "fsck": ("org.apache.hadoop.hdfs.tools.DFSck",
      "run a DFS filesystem checking utility"),
  "balancer": ("org.apache.hadoop.hdfs.server.balancer.Balancer",
      "run a cluster balancing utility"),
  "jmxget": ("org.apache.hadoop.hdfs.tools.JMXGet",
      "get JMX exported values from NameNode or DataNode"),
  "oiv": ("org.apache.hadoop.hdfs.tools.offlineImageViewer.OfflineImageViewer",
      "apply the offline fsimage viewer to an fsimage"),
  "oev": ("org.apache.hadoop.hdfs.tools.offlineEditsViewer.OfflineEditsViewer",
      "apply the offline edits viewer to an edits file"),
  "fetchdt": ("org.apache.hadoop.hdfs.tools.DelegationTokenFetcher",
      "fetch a delegation token from the NameNode"),
  "getconf": ("org.apache.hadoop.hdfs.tools.GetConf",
      "get config values from configuration"),
  "groups": ("org.apache.hadoop.hdfs.tools.GetGroups",
      "get the groups which users belong to"),
}

def generate_metrics_config(args, host, job_name, instance_id=-1):
  job = args.hdfs_config.jobs[job_name]

  supervisor_client = deploy_utils.get_supervisor_client(host,
      "hdfs", args.hdfs_config.cluster.name, job_name, instance_id=instance_id)

  ganglia_switch = "# "
  if args.hdfs_config.cluster.ganglia_address:
    ganglia_switch = ""
  config_dict = {
      "job_name": job_name,
      "period": 10,
      "data_dir": supervisor_client.get_log_dir(),
      "ganglia_address": args.hdfs_config.cluster.ganglia_address,
      "ganglia_switch": ganglia_switch,
  }

  local_path = "%s/hadoop-metrics2.properties.tmpl" % deploy_utils.get_template_dir()
  template = deploy_utils.Template(open(local_path, "r").read())
  return template.substitute(config_dict)

def generate_configs(args, host, job_name, instance_id):
  core_site_xml = deploy_utils.generate_site_xml(args,
    args.hdfs_config.configuration.generated_files["core-site.xml"])
  hdfs_site_xml = deploy_utils.generate_site_xml(args,
    args.hdfs_config.configuration.generated_files["hdfs-site.xml"])
  hadoop_metrics2_properties = generate_metrics_config(args, host, job_name, instance_id)

  config_files = {
    "core-site.xml": core_site_xml,
    "hdfs-site.xml": hdfs_site_xml,
    "hadoop-metrics2.properties": hadoop_metrics2_properties,
  }
  config_files.update(args.hdfs_config.configuration.raw_files)

  return config_files

def generate_run_scripts_params(args, host, job_name, host_id, instance_id):
  job = args.hdfs_config.jobs[job_name]

  supervisor_client = deploy_utils.get_supervisor_client(host,
      "hdfs", args.hdfs_config.cluster.name, job_name, instance_id=instance_id)

  artifact_and_version = "hadoop-" + args.hdfs_config.cluster.version

  jar_dirs = ""
  # must include both [dir]/ and [dir]/* as [dir]/* only import all jars under
  # this dir but we also need access the webapps under this dir.
  for component in ["common", "hdfs"]:
    if jar_dirs: jar_dirs += ":"
    component_dir = ("$package_dir/share/hadoop/%s" % component)
    jar_dirs += "%s/:%s/lib/*:%s/*" % (
        component_dir, component_dir, component_dir)
  log_level = deploy_utils.get_service_log_level(args, args.hdfs_config)

  params = job.get_arguments(args, args.hdfs_config.cluster, args.hdfs_config.jobs,
    args.hdfs_config.arguments_dict, job_name, host_id, instance_id)

  script_dict = {
      "artifact": artifact_and_version,
      "job_name": job_name,
      "jar_dirs": jar_dirs,
      "run_dir": supervisor_client.get_run_dir(),
      "params": params,
  }

  return script_dict

def get_hdfs_service_config(args):
  args.hdfs_config = deploy_utils.get_service_config(args)
  if not args.hdfs_config.cluster.zk_cluster:
    Log.print_critical(
        "hdfs cluster must depends on a zookeeper clusters: %s" %
        args.hdfs_config.cluster.name)

  namenode_hosts = args.hdfs_config.jobs["namenode"].hosts
  args.hdfs_config.jobs["zkfc"].hosts = namenode_hosts.copy()
  args.skip_gen_config_files = False

def generate_bootstrap_script(args, host, job_name, host_id, instance_id, active):
  option = str()
  script_params = generate_run_scripts_params(args, host, job_name, host_id, instance_id)
  script_params['ha_status'] = 'standby'
  if job_name == "zkfc":
    if active:
      option = "-formatZK"
      script_params['ha_status'] = 'active'
  elif job_name == "namenode":
    if active:
      option = "-format -nonInteractive"
    else:
      option = "-bootstrapStandby -skipSharedEditsCheck -nonInteractive"
  script_params['params'] += " %s" % option

  return deploy_utils.create_run_script(
      '%s/bootstrap_hdfs.sh.tmpl' % deploy_utils.get_template_dir(),
      script_params)

def generate_cleanup_script(args, host, job_name, host_id, instance_id, active):
  script_params = generate_run_scripts_params(args, host, job_name, host_id, instance_id)
  script_params['params'] += " -clearZK"
  if active:
    script_params['ha_status'] = 'active'
  else:
    script_params['ha_status'] = 'standby'
  return deploy_utils.create_run_script(
      '%s/cleanup_hdfs.sh.tmpl' % deploy_utils.get_template_dir(),
      script_params)

def generate_start_script(args, host, job_name, host_id, instance_id):
  script_params = generate_run_scripts_params(args, host, job_name, host_id, instance_id)
  return deploy_utils.create_run_script(
      '%s/start.sh.tmpl' % deploy_utils.get_template_dir(),
      script_params)

def check_journalnode_all_started(args):
  job = args.hdfs_config.jobs["journalnode"]
  hosts = job.hosts
  for host_id in hosts.iterkeys():
    for instance_id in range(hosts[host_id].instance_num):
      if not deploy_utils.check_service(hosts[host_id].ip,
        service_config.get_base_port(job.base_port, instance_id)):
        return False
  return True

def get_data_dir_indexes(args, job_name, host, instance_id):
  if job_name != "datanode":
    return "0"
  else:
    supervisor_client = deploy_utils.get_supervisor_client(host,
        "hdfs", args.hdfs_config.cluster.name, job_name, instance_id=instance_id)
    data_dirs = supervisor_client.get_available_data_dirs()
    return ",".join([str(i) for i in range(len(data_dirs))])

def install(args):
  get_hdfs_service_config(args)
  deploy_utils.install_service(args, "hdfs", args.hdfs_config, "hadoop")

def cleanup_job(args, host, job_name, host_id, instance_id, active, cleanup_token):
  cleanup_script = str()
  if job_name == "zkfc":
    cleanup_script = generate_cleanup_script(args, host, job_name, host_id, instance_id, active)
  deploy_utils.cleanup_job("hdfs", args.hdfs_config,
      host, job_name, instance_id, cleanup_token, cleanup_script)

def cleanup(args):
  get_hdfs_service_config(args)

  cleanup_token = deploy_utils.confirm_cleanup(args,
      "hdfs", args.hdfs_config)

  first = True
  for job_name in args.job or ALL_JOBS:
    hosts = args.hdfs_config.jobs[job_name].hosts

    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        cleanup_job(args, hosts[host_id].ip, job_name, host_id, instance_id, first, cleanup_token)
        if job_name == "zkfc":
          first = False

def bootstrap_job(args, host, job_name, host_id, instance_id, active, cleanup_token):
  # parse the service_config according to the instance_id
  args.hdfs_config.parse_generated_config_files(args, job_name, host_id, instance_id)
  data_dir_indexes = get_data_dir_indexes(args, job_name, host, instance_id)
  config_files = generate_configs(args, host, job_name, instance_id)
  if job_name == "namenode" or job_name == "zkfc":
    bootstrap_script = generate_bootstrap_script(args, host, job_name, host_id, instance_id, active)
    deploy_utils.bootstrap_job(args, "hadoop", "hdfs", args.hdfs_config,
        host, job_name, instance_id, cleanup_token, data_dir_indexes, bootstrap_script,
        **config_files)
  else:
    deploy_utils.bootstrap_job(args, "hadoop", "hdfs", args.hdfs_config,
        host, job_name, instance_id, cleanup_token, data_dir_indexes, '', **config_files)
  # start job after bootstrapping
  args.skip_gen_config_files = True
  start_job(args, host, job_name, host_id, instance_id)

def bootstrap(args):
  get_hdfs_service_config(args)

  cleanup_token = deploy_utils.confirm_bootstrap("hdfs", args.hdfs_config)

  for job_name in args.job or ALL_JOBS:
    hosts = args.hdfs_config.jobs[job_name].hosts
    first = True
    if job_name == "namenode":
      while not check_journalnode_all_started(args):
        Log.print_warning("Wait for journalnode starting")
        time.sleep(2)
    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        if job_name == "namenode" and not first:
          while not deploy_utils.check_service(hosts[0].ip,
              args.hdfs_config.jobs["namenode"].base_port):
            Log.print_warning("Wait for active namenode starting")
            time.sleep(2)

        bootstrap_job(args, hosts[host_id].ip, job_name, host_id, instance_id, first, cleanup_token)
        first = False

def start_job(args, host, job_name, host_id, instance_id):
  # parse the service_config according to the instance_id
  args.hdfs_config.parse_generated_config_files(args, job_name, host_id, instance_id)
  start_script = generate_start_script(args, host, job_name, host_id, instance_id)
  http_url = deploy_utils.get_http_service_uri(host,
    args.hdfs_config.jobs[job_name].base_port, instance_id)
  config_files = dict()
  if not args.skip_gen_config_files:
    config_files = generate_configs(args, host, job_name, instance_id)
  deploy_utils.start_job(args, "hadoop", "hdfs", args.hdfs_config,
      host, job_name, instance_id, start_script, http_url, **config_files)

def start(args):
  if not args.skip_confirm:
    deploy_utils.confirm_start(args)
  get_hdfs_service_config(args)

  for job_name in args.job or ALL_JOBS:
    hosts = args.hdfs_config.jobs[job_name].hosts
    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        start_job(args, hosts[host_id].ip, job_name, host_id, instance_id)

def stop_job(args, host, job_name, instance_id):
  deploy_utils.stop_job("hdfs", args.hdfs_config,
      host, job_name, instance_id)

def stop(args):
  if not args.skip_confirm:
    deploy_utils.confirm_stop(args)
  get_hdfs_service_config(args)

  for job_name in args.job or ALL_JOBS:
    hosts = args.hdfs_config.jobs[job_name].hosts
    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        stop_job(args, hosts[host_id].ip, job_name, instance_id)

def restart(args):
  if not args.skip_confirm:
    deploy_utils.confirm_restart(args)
  get_hdfs_service_config(args)

  for job_name in args.job or ALL_JOBS:
    hosts = args.hdfs_config.jobs[job_name].hosts
    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        stop_job(args, hosts[host_id].ip, job_name, instance_id)

  for job_name in args.job or ALL_JOBS:
    hosts = args.hdfs_config.jobs[job_name].hosts
    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        deploy_utils.wait_for_job_stopping("hdfs",
          args.hdfs_config.cluster.name, job_name, hosts[host_id].ip, instance_id)
        start_job(args, hosts[host_id].ip, job_name, host_id, instance_id)

def show(args):
  get_hdfs_service_config(args)

  for job_name in args.job or ALL_JOBS:
    hosts = args.hdfs_config.jobs[job_name].hosts
    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        deploy_utils.show_job("hdfs", args.hdfs_config, hosts[host_id].ip,
          job_name, instance_id)

def run_shell(args):
  get_hdfs_service_config(args)

  main_class, options = deploy_utils.parse_shell_command(
      args, SHELL_COMMAND_INFO)
  if not main_class:
    return
  # parse the service_config, suppose the instance_id is -1
  args.hdfs_config.parse_generated_config_files(args)
  core_site_dict = args.hdfs_config.configuration.generated_files["core-site.xml"]
  hdfs_site_dict = args.hdfs_config.configuration.generated_files["hdfs-site.xml"]

  hadoop_opts = list()
  for key, value in core_site_dict.iteritems():
    hadoop_opts.append("-D%s%s=%s" % (deploy_utils.HADOOP_PROPERTY_PREFIX,
          key, value))
  for key, value in hdfs_site_dict.iteritems():
    hadoop_opts.append("-D%s%s=%s" % (deploy_utils.HADOOP_PROPERTY_PREFIX,
          key, value))

  package_root = deploy_utils.get_artifact_package_root(args,
    args.hdfs_config.cluster, "hadoop")
  lib_root = "%s/share/hadoop" % package_root
  class_path = "%s/etc/hadoop" % package_root
  for component in ["common", "hdfs"]:
    component_dir = "%s/%s" % (lib_root, component)
    class_path += ":%s/:%s/*:%s/lib/*" % (component_dir,
        component_dir, component_dir)

  if deploy_utils.is_security_enabled(args):
    boot_class_path = "%s/common/lib/hadoop-security-%s.jar" % (lib_root,
        args.hdfs_config.cluster.version)
    hadoop_opts.append("-Xbootclasspath/p:%s" % boot_class_path)
    hadoop_opts.append("-Dkerberos.instance=hadoop")
    hadoop_opts.append(
        "-Djava.security.krb5.conf=%s/krb5-hadoop.conf" %
        deploy_utils.get_config_dir())

  cmd = (["java", "-cp", class_path] + hadoop_opts +
      [main_class] + options)
  p = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
  p.wait()

def generate_client_config(args, artifact, version):
  config_path = "%s/%s/%s-%s/etc/hadoop" % (args.package_root,
      args.cluster, artifact, version)
  deploy_utils.write_file("%s/core-site.xml" % config_path,
      deploy_utils.generate_site_xml(args,
        args.hdfs_config.configuration.generated_files["core-site.xml"]))
  deploy_utils.write_file("%s/hdfs-site.xml" % config_path,
      deploy_utils.generate_site_xml(args,
        args.hdfs_config.configuration.generated_files["hdfs-site.xml"]))
  deploy_utils.write_file("%s/hadoop-metrics2.properties" % config_path,
      generate_metrics_config(args, args.hdfs_config.jobs["namenode"].hosts[0].ip,
        "namenode"))
  deploy_utils.write_file("%s/krb5.conf" % config_path,
      args.hdfs_config.configuration.raw_files["krb5.conf"])
  update_hadoop_env_sh(args, artifact, version, "HADOOP_OPTS")

def update_hadoop_env_sh(args, artifact, version, opts_name):
  config_path = "%s/%s/%s-%s/etc/hadoop" % (args.package_root,
      args.cluster, artifact, version)
  hadoop_opts = "-Djava.security.krb5.conf=$HADOOP_CONF_DIR/krb5.conf"
  deploy_utils.append_to_file("%s/hadoop-env.sh" % config_path,
      'export %s="$%s %s"\n' % (opts_name, opts_name, hadoop_opts))

def pack(args):
  get_hdfs_service_config(args)
  args.hdfs_config.parse_generated_config_files(args)

  version = args.hdfs_config.cluster.version
  deploy_utils.make_package_dir(args, "hadoop", args.hdfs_config.cluster)
  generate_client_config(args, "hadoop", version)

  if not args.skip_tarball:
    deploy_utils.pack_package(args, "hadoop", args.hdfs_config.cluster.version)
  Log.print_success("Pack client utilities for hadoop success!\n")

def rolling_update(args):
  if not args.job:
    Log.print_critical("You must specify the job name to do rolling update")

  get_hdfs_service_config(args)
  job_name = args.job[0]

  if not args.skip_confirm:
    deploy_utils.confirm_action(args, "rolling_update")

  Log.print_info("Rolling updating %s" % job_name)
  hosts = args.hdfs_config.jobs[job_name].hosts
  wait_time = 0

  args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
  for host_id in args.task_map.keys() or hosts.iterkeys():
    for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
      instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
      deploy_utils.confirm_rolling_update(host_id, instance_id, wait_time)
      stop_job(args, hosts[host_id].ip, job_name, instance_id)
      deploy_utils.wait_for_job_stopping("hdfs",
        args.hdfs_config.cluster.name, job_name, hosts[host_id].ip, instance_id)
      start_job(args, hosts[host_id].ip, job_name, host_id, instance_id)
      deploy_utils.wait_for_job_starting("hdfs",
        args.hdfs_config.cluster.name, job_name, hosts[host_id].ip, instance_id)
      wait_time = args.time_interval
  Log.print_success("Rolling updating %s success" % job_name)

if __name__ == '__main__':
  test()
