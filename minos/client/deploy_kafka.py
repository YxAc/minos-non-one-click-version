#!/usr/bin/env python

import argparse
import os
import service_config
import subprocess
import sys
import urlparse

import deploy_utils

from log import Log

ALL_JOBS = ["kafka"]

def _get_kafka_service_config(args):
  args.kafka_config = deploy_utils.get_service_config(args)

def generate_configs(args, job_name, host_id, instance_id):
  hosts = args.kafka_config.jobs[job_name].hosts
  task_id = deploy_utils.get_task_id(hosts, host_id, instance_id)

  kafka_cfg_dict = args.kafka_config.configuration.generated_files["kafka.cfg"]
  kafka_cfg_dict["broker.id"] = task_id
  kafka_cfg = deploy_utils.generate_properties_file(args, kafka_cfg_dict)

  config_files = {
    "kafka.cfg": kafka_cfg,
  }
  config_files.update(args.kafka_config.configuration.raw_files)

  return config_files

def generate_run_scripts_params(args, host, job_name, host_id, instance_id):
  job = args.kafka_config.jobs[job_name]

  supervisor_client = deploy_utils.get_supervisor_client(host,
      "kafka", args.kafka_config.cluster.name, job_name, instance_id=instance_id)

  artifact_and_version = "kafka-" + args.kafka_config.cluster.version

  component_dir = "$package_dir"
  # must include both [dir]/ and [dir]/* as [dir]/* only import all jars under
  # this dir but we also need access the webapps under this dir.
  jar_dirs = "%s/:%s/libs/*:%s/*" % (component_dir, component_dir, component_dir)
  log_level = deploy_utils.get_service_log_level(args, args.kafka_config)

  params = job.get_arguments(args, args.kafka_config.cluster, args.kafka_config.jobs,
    args.kafka_config.arguments_dict, job_name, host_id, instance_id)

  script_dict = {
      "artifact": artifact_and_version,
      "job_name": job_name,
      "jar_dirs": jar_dirs,
      "run_dir": supervisor_client.get_run_dir(),
      "params": params,
  }

  return script_dict

def generate_start_script(args, host, job_name, host_id, instance_id):
  script_params = generate_run_scripts_params(args, host, job_name, host_id, instance_id)
  return deploy_utils.create_run_script(
      "%s/start.sh.tmpl" % deploy_utils.get_template_dir(),
      script_params)

def install(args):
  _get_kafka_service_config(args)
  deploy_utils.install_service(args, "kafka", args.kafka_config, "kafka")

def cleanup(args):
  _get_kafka_service_config(args)

  cleanup_token = deploy_utils.confirm_cleanup(args,
      "kafka", args.kafka_config)
  for job_name in args.job or ALL_JOBS:
    hosts = args.kafka_config.jobs[job_name].hosts
    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        deploy_utils.cleanup_job("kafka", args.kafka_config,
          hosts[host_id].ip, job_name, instance_id, cleanup_token)

def bootstrap_job(args, host, job_name, host_id, instance_id, cleanup_token):
  # parse the service_config according to the instance_id
  args.kafka_config.parse_generated_config_files(args, job_name, host_id, instance_id)
  deploy_utils.bootstrap_job(args, "kafka", "kafka",
      args.kafka_config, host, job_name, instance_id, cleanup_token, '0')
  start_job(args, host, job_name, host_id, instance_id)

def bootstrap(args):
  _get_kafka_service_config(args)
  cleanup_token = deploy_utils.confirm_bootstrap("kafka", args.kafka_config)

  for job_name in args.job or ALL_JOBS:
    hosts = args.kafka_config.jobs[job_name].hosts
    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        bootstrap_job(args, hosts[host_id].ip, job_name, host_id, instance_id, cleanup_token)

def start_job(args, host, job_name, host_id, instance_id):
  # parse the service_config according to the instance_id
  args.kafka_config.parse_generated_config_files(args, job_name, host_id, instance_id)

  config_files = generate_configs(args, job_name, host_id, instance_id)
  start_script = generate_start_script(args, host, job_name, host_id, instance_id)
  http_url = deploy_utils.get_http_service_uri(host,
    args.kafka_config.jobs[job_name].base_port, instance_id)
  deploy_utils.start_job(args, "kafka", "kafka", args.kafka_config,
      host, job_name, instance_id, start_script, http_url, **config_files)

def start(args):
  if not args.skip_confirm:
    deploy_utils.confirm_start(args)
  _get_kafka_service_config(args)

  for job_name in args.job or ALL_JOBS:
    hosts = args.kafka_config.jobs[job_name].hosts
    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        start_job(args, hosts[host_id].ip, job_name, host_id, instance_id)

def stop_job(args, host, job_name, instance_id):
  deploy_utils.stop_job("kafka", args.kafka_config, host, job_name, instance_id)

def stop(args):
  if not args.skip_confirm:
    deploy_utils.confirm_stop(args)
  _get_kafka_service_config(args)

  for job_name in args.job or ALL_JOBS:
    hosts = args.kafka_config.jobs[job_name].hosts
    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        stop_job(args, hosts[host_id].ip, job_name, instance_id)

def restart(args):
  if not args.skip_confirm:
    deploy_utils.confirm_restart(args)
  _get_kafka_service_config(args)

  for job_name in args.job or ALL_JOBS:
    hosts = args.kafka_config.jobs[job_name].hosts
    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        stop_job(args, hosts[host_id].ip, job_name, instance_id)

  for job_name in args.job or ALL_JOBS:
    hosts = args.kafka_config.jobs[job_name].hosts
    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        deploy_utils.wait_for_job_stopping("kafka",
          args.kafka_config.cluster.name, job_name, hosts[host_id].ip, instance_id)
        start_job(args, hosts[host_id].ip, job_name, host_id, instance_id)

def show(args):
  _get_kafka_service_config(args)

  for job_name in args.job or ALL_JOBS:
    hosts = args.kafka_config.jobs[job_name].hosts
    args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
    for host_id in args.task_map.keys() or hosts.keys():
      for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
        instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
        deploy_utils.show_job("kafka", args.kafka_config,
          hosts[host_id].ip, job_name, instance_id)

def run_shell(args):
  Log.print_critical("'shell' command is not supported!")

def pack(args):
  Log.print_critical("'pack' command is not supported!")

def rolling_update(args):
  if not args.job:
    Log.print_critical("You must specify the job name to do rolling update")

  _get_kafka_service_config(args)
  job_name = args.job[0]

  if not args.skip_confirm:
    deploy_utils.confirm_action(args, "rolling_update")

  Log.print_info("Rolling updating %s" % job_name)
  hosts = args.kafka_config.jobs[job_name].hosts
  wait_time = 0

  args.task_map = deploy_utils.parse_args_host_and_task(args, hosts)
  for host_id in args.task_map.keys() or hosts.iterkeys():
    for instance_id in args.task_map.get(host_id) or range(hosts[host_id].instance_num):
      instance_id = -1 if not deploy_utils.is_multiple_instances(host_id, hosts) else instance_id
      deploy_utils.confirm_rolling_update(host_id, instance_id, wait_time)
      stop_job(args, hosts[host_id].ip, job_name, instance_id)
      deploy_utils.wait_for_job_stopping("kafka",
        args.kafka_config.cluster.name, job_name, hosts[host_id].ip, instance_id)
      start_job(args, hosts[host_id].ip, job_name, host_id, instance_id)
      deploy_utils.wait_for_job_starting("kafka",
        args.kafka_config.cluster.name, job_name, hosts[host_id].ip, instance_id)
      wait_time = args.time_interval
  Log.print_success("Rolling updating %s success" % job_name)

if __name__ == '__main__':
  test()
