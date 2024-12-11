import time
import logging
import pytest
import re
from datetime import datetime
from tests.common.utilities import wait_until
from tests.common.helpers.gnmi_utils import GNMIEnvironment


logger = logging.getLogger(__name__)
GNMI_CONTAINER_NAME = ''
GNMI_PROGRAM_NAME = ''
GNMI_PORT = 0
GNMI_CA = 'gnmiCA.pem'
GNMI_CA_KEY = 'gnmiCA.key'
SERVER_CRT = 'gnmiserver.crt'
SERVER_KEY = 'gnmiserver.key'
SERVER_CSR = 'gnmiserver.csr'
CLIENT_CRT = 'gnmiclient.crt'
CLIENT_KEY = 'gnmiclient.key'
CLIENT_CSR = 'gnmiclient.csr'
CRT_DST_PATH = '/etc/sonic/telemetry/'
# Wait 15 seconds after starting GNMI server
GNMI_SERVER_START_WAIT_TIME = 15


def gnmi_container(duthost):
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    return env.gnmi_container


def create_ext_conf(ip, filename):
    text = '''
[ req_ext ]
subjectAltName = @alt_names
[alt_names]
DNS.1   = hostname.com
IP      = %s
''' % ip
    with open(filename, 'w') as file:
        file.write(text)
    return


def dump_gnmi_log(duthost):
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    dut_command = "docker exec %s cat /root/gnmi.log" % (env.gnmi_container)
    res = duthost.shell(dut_command, module_ignore_errors=True)
    logger.info("GNMI log: " + res['stdout'])


def dump_system_status(duthost):
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    dut_command = "docker exec %s ps -efwww" % (env.gnmi_container)
    res = duthost.shell(dut_command, module_ignore_errors=True)
    logger.info("GNMI process: " + res['stdout'])
    dut_command = "docker exec %s date" % (env.gnmi_container)
    res = duthost.shell(dut_command, module_ignore_errors=True)
    logger.info("System time: " + res['stdout'] + res['stderr'])


def verify_tcp_port(localhost, ip, port):
    command = "ssh  -o ConnectTimeout=3 -v -p %s %s" % (port, ip)
    res = localhost.shell(command, module_ignore_errors=True)
    logger.info("TCP: " + res['stdout'] + res['stderr'])


def add_gnmi_client_common_name(duthost, cname):
    duthost.shell('sudo sonic-db-cli CONFIG_DB hset "GNMI_CLIENT_CERT|{}" "role" "role1"'.format(cname),
                  module_ignore_errors=True)


def del_gnmi_client_common_name(duthost, cname):
    duthost.shell('sudo sonic-db-cli CONFIG_DB del "GNMI_CLIENT_CERT|{}"'.format(cname), module_ignore_errors=True)


def apply_cert_config(duthost):
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    # Stop all running program
    dut_command = "docker exec %s supervisorctl status" % (env.gnmi_container)
    output = duthost.shell(dut_command, module_ignore_errors=True)
    for line in output['stdout_lines']:
        res = line.split()
        if len(res) < 3:
            continue
        program = res[0]
        status = res[1]
        if status == "RUNNING":
            dut_command = "docker exec %s supervisorctl stop %s" % (env.gnmi_container, program)
            duthost.shell(dut_command, module_ignore_errors=True)
    dut_command = "docker exec %s pkill %s" % (env.gnmi_container, env.gnmi_process)
    duthost.shell(dut_command, module_ignore_errors=True)
    dut_command = "docker exec %s bash -c " % env.gnmi_container
    dut_command += "\"/usr/bin/nohup /usr/sbin/%s -logtostderr --port %s " % (env.gnmi_process, env.gnmi_port)
    dut_command += "--server_crt {}{} --server_key {}{} ".format(CRT_DST_PATH, SERVER_CRT, CRT_DST_PATH, SERVER_KEY)
    dut_command += "--config_table_name GNMI_CLIENT_CERT "
    dut_command += "--client_auth cert "
    dut_command += "--ca_crt {}{} ".format(CRT_DST_PATH, GNMI_CA)
    dut_command += "-gnmi_native_write=true -v=10 >/root/gnmi.log 2>&1 &\""
    duthost.shell(dut_command)

    # Setup gnmi client cert common name
    add_gnmi_client_common_name(duthost, "test.client.gnmi.sonic")

    time.sleep(GNMI_SERVER_START_WAIT_TIME)
    dut_command = "sudo netstat -nap | grep %d" % env.gnmi_port
    output = duthost.shell(dut_command, module_ignore_errors=True)
    if env.gnmi_process not in output['stdout']:
        # Dump tcp port status and gnmi log
        logger.info("TCP port status: " + output['stdout'])
        dump_gnmi_log(duthost)
        dump_system_status(duthost)
        pytest.fail("Failed to start gnmi server")


def check_gnmi_status(duthost):
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    dut_command = "docker exec %s supervisorctl status %s" % (env.gnmi_container, env.gnmi_program)
    output = duthost.shell(dut_command, module_ignore_errors=True)
    return "RUNNING" in output['stdout']


def recover_cert_config(duthost):
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    cmds = [
        'systemctl reset-failed %s' % (env.gnmi_container),
        'systemctl restart %s' % (env.gnmi_container)
    ]
    duthost.shell_cmds(cmds=cmds)

    # Remove gnmi client cert common name
    del_gnmi_client_common_name(duthost, "test.client.gnmi.sonic")
    assert wait_until(60, 3, 0, check_gnmi_status, duthost), "GNMI service failed to start"


def gnmi_capabilities(duthost, localhost):
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    ip = duthost.mgmt_ip
    port = env.gnmi_port
    # Run gnmi_cli in gnmi container as workaround
    cmd = "docker exec %s gnmi_cli -client_types=gnmi -a %s:%s " % (env.gnmi_container, ip, port)
    cmd += "-client_crt {}{} ".format(CRT_DST_PATH, CLIENT_CRT)
    cmd += "-client_key {}{} ".format(CRT_DST_PATH, CLIENT_KEY)
    cmd += "-ca_crt {}{} ".format(CRT_DST_PATH, GNMI_CA)
    cmd += "-logtostderr -capabilities"
    output = duthost.shell(cmd, module_ignore_errors=True)
    if output['stderr']:
        dump_gnmi_log(duthost)
        dump_system_status(duthost)
        verify_tcp_port(localhost, ip, port)
        return -1, output['stderr']
    else:
        return 0, output['stdout']


def gnmi_set(duthost, ptfhost, delete_list, update_list, replace_list):
    """
    Send GNMI set request with GNMI client

    Args:
        duthost: fixture for duthost
        ptfhost: fixture for ptfhost
        delete_list: list for delete operations
        update_list: list for update operations
        replace_list: list for replace operations

    Returns:
    """
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    ip = duthost.mgmt_ip
    port = env.gnmi_port
    cmd = 'python2 /root/gnxi/gnmi_cli_py/py_gnmicli.py '
    cmd += '--timeout 30 '
    cmd += '-t %s -p %u ' % (ip, port)
    cmd += '-xo sonic-db '
    cmd += '-rcert /root/%s ' % GNMI_CA
    cmd += '-pkey /root/%s ' % CLIENT_KEY
    cmd += '-cchain /root/%s ' % CLIENT_CRT
    cmd += '-m set-update '
    xpath = ''
    xvalue = ''
    for path in delete_list:
        path = path.replace('sonic-db:', '')
        xpath += ' ' + path
        xvalue += ' ""'
    for update in update_list:
        update = update.replace('sonic-db:', '')
        result = update.rsplit(':', 1)
        xpath += ' ' + result[0]
        xvalue += ' ' + result[1]
    for replace in replace_list:
        replace = replace.replace('sonic-db:', '')
        result = replace.rsplit(':', 1)
        xpath += ' ' + result[0]
        if '#' in result[1]:
            xvalue += ' ""'
        else:
            xvalue += ' ' + result[1]
    cmd += '--xpath ' + xpath
    cmd += ' '
    cmd += '--value ' + xvalue
    output = ptfhost.shell(cmd, module_ignore_errors=True)
    error = "GRPC error\n"
    if error in output['stdout']:
        dump_gnmi_log(duthost)
        dump_system_status(duthost)
        result = output['stdout'].split(error, 1)
        raise Exception("GRPC error:" + result[1])
    if output['stderr']:
        dump_gnmi_log(duthost)
        dump_system_status(duthost)
        raise Exception("error:" + output['stderr'])
    else:
        return


def gnmi_get(duthost, ptfhost, path_list):
    """
    Send GNMI get request with GNMI client

    Args:
        duthost: fixture for duthost
        ptfhost: fixture for ptfhost
        path_list: list for get path

    Returns:
        msg_list: list for get result
    """
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    ip = duthost.mgmt_ip
    port = env.gnmi_port
    cmd = 'python2 /root/gnxi/gnmi_cli_py/py_gnmicli.py '
    cmd += '--timeout 30 '
    cmd += '-t %s -p %u ' % (ip, port)
    cmd += '-xo sonic-db '
    cmd += '-rcert /root/%s ' % GNMI_CA
    cmd += '-pkey /root/%s ' % CLIENT_KEY
    cmd += '-cchain /root/%s ' % CLIENT_CRT
    cmd += '--encoding 4 '
    cmd += '-m get '
    cmd += '--xpath '
    for path in path_list:
        path = path.replace('sonic-db:', '')
        cmd += " " + path
    output = ptfhost.shell(cmd, module_ignore_errors=True)
    if output['stderr']:
        raise Exception("error:" + output['stderr'])
    else:
        msg = output['stdout'].replace('\\', '')
        error = "GRPC error\n"
        if error in msg:
            dump_gnmi_log(duthost)
            dump_system_status(duthost)
            result = msg.split(error, 1)
            raise Exception("GRPC error:" + result[1])
        mark = 'The GetResponse is below\n' + '-'*25 + '\n'
        if mark in msg:
            result = msg.split(mark, 1)
            msg_list = result[1].split('-'*25)[0:-1]
            return [msg.strip("\n") for msg in msg_list]
        else:
            dump_gnmi_log(duthost)
            dump_system_status(duthost)
            raise Exception("error:" + msg)


# py_gnmicli does not fully support POLLING mode
# Use gnmi_cli instead
def gnmi_subscribe_polling(duthost, ptfhost, path_list, interval_ms, count):
    """
    Send GNMI subscribe request with GNMI client

    Args:
        duthost: fixture for duthost
        ptfhost: fixture for ptfhost
        path_list: list for get path
        interval_ms: interval, unit is ms
        count: update count

    Returns:
        msg: gnmi client output
    """
    if path_list is None:
        logger.error("path_list is None")
        return "", ""
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    ip = duthost.mgmt_ip
    port = env.gnmi_port
    interval = interval_ms / 1000.0
    # Run gnmi_cli in gnmi container as workaround
    cmd = "docker exec %s gnmi_cli -client_types=gnmi -a %s:%s " % (env.gnmi_container, ip, port)
    cmd += "-client_crt {}{} ".format(CRT_DST_PATH,CLIENT_CRT)
    cmd += "-client_key {}{} ".format(CRT_DST_PATH, CLIENT_KEY)
    cmd += "-ca_crt {}{} ".format(CRT_DST_PATH, GNMI_CA)
    cmd += "-logtostderr "
    # Use sonic-db as default origin
    cmd += '-origin=sonic-db '
    cmd += '-query_type=polling '
    cmd += '-polling_interval %us -count %u ' % (int(interval), count)
    for path in path_list:
        path = path.replace('sonic-db:', '')
        cmd += '-q %s ' % (path)
    output = duthost.shell(cmd, module_ignore_errors=True)
    return output['stdout'], output['stderr']


def gnmi_subscribe_streaming_sample(duthost, ptfhost, path_list, interval_ms, count):
    """
    Send GNMI subscribe request with GNMI client

    Args:
        duthost: fixture for duthost
        ptfhost: fixture for ptfhost
        path_list: list for get path
        interval_ms: interval, unit is ms
        count: update count

    Returns:
        msg: gnmi client output
    """
    if path_list is None:
        logger.error("path_list is None")
        return "", ""
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    ip = duthost.mgmt_ip
    port = env.gnmi_port
    cmd = 'python2 /root/gnxi/gnmi_cli_py/py_gnmicli.py '
    cmd += '--timeout 30 '
    cmd += '-t %s -p %u ' % (ip, port)
    cmd += '-xo sonic-db '
    cmd += '-rcert /root/%s ' % GNMI_CA
    cmd += '-pkey /root/%s ' % CLIENT_KEY
    cmd += '-cchain /root/%s ' %CLIENT_CRT
    cmd += '--encoding 4 '
    cmd += '-m subscribe '
    cmd += '--subscribe_mode 0 --submode 2 --create_connections 1 '
    cmd += '--interval %u --update_count %u ' % (interval_ms, count)
    cmd += '--xpath '
    for path in path_list:
        path = path.replace('sonic-db:', '')
        cmd += " " + path
    output = ptfhost.shell(cmd, module_ignore_errors=True)
    msg = output['stdout'].replace('\\', '')
    return msg, output['stderr']


def gnmi_subscribe_streaming_onchange(duthost, ptfhost, path_list, count):
    """
    Send GNMI subscribe request with GNMI client

    Args:
        duthost: fixture for duthost
        ptfhost: fixture for ptfhost
        path_list: list for get path
        count: update count

    Returns:
        msg: gnmi client output
    """
    if path_list is None:
        logger.error("path_list is None")
        return "", ""
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    ip = duthost.mgmt_ip
    port = env.gnmi_port
    cmd = 'python2 /root/gnxi/gnmi_cli_py/py_gnmicli.py '
    cmd += '--timeout 30 '
    cmd += '-t %s -p %u ' % (ip, port)
    cmd += '-xo sonic-db '
    cmd += '-rcert /root/%s ' % GNMI_CA
    cmd += '-pkey /root/%s ' % CLIENT_KEY
    cmd += '-cchain /root/%s ' % CLIENT_CRT
    cmd += '--encoding 4 '
    cmd += '-m subscribe '
    cmd += '--subscribe_mode 0 --submode 1 --create_connections 1 '
    cmd += '--update_count %u ' % count
    cmd += '--xpath '
    for path in path_list:
        path = path.replace('sonic-db:', '')
        cmd += " " + path
    output = ptfhost.shell(cmd, module_ignore_errors=True)
    msg = output['stdout'].replace('\\', '')
    return msg, output['stderr']


def gnoi_reboot(duthost, method, delay, message):
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    ip = duthost.mgmt_ip
    port = env.gnmi_port
    # Run gnoi_client in gnmi container as workaround
    cmd = "docker exec %s gnoi_client -target %s:%s " % (env.gnmi_container, ip, port)
    cmd += "-cert {}{} ".format(CRT_DST_PATH, CLIENT_CRT)
    cmd += "-key {}{} ".format(CRT_DST_PATH, CLIENT_KEY)
    cmd += "-ca {}{} ".format(CRT_DST_PATH, GNMI_CA)
    cmd += "-logtostderr -rpc Reboot "
    cmd += '-jsonin "{\\\"method\\\":%d, \\\"delay\\\":%d, \\\"message\\\":\\\"%s\\\"}"' % (method, delay, message)
    output = duthost.shell(cmd, module_ignore_errors=True)
    if output['stderr']:
        logger.error(output['stderr'])
        return -1, output['stderr']
    else:
        return 0, output['stdout']


def gnoi_request(duthost, localhost, rpc, request_json_data):
    env = GNMIEnvironment(duthost, GNMIEnvironment.GNMI_MODE)
    ip = duthost.mgmt_ip
    port = env.gnmi_port
    cmd = "docker exec %s gnoi_client -target %s:%s " % (env.gnmi_container, ip, port)
    cmd += "-cert {}{} ".format(CRT_DST_PATH, CLIENT_CRT)
    cmd += "-key {}{} ".format(CRT_DST_PATH, CLIENT_KEY)
    cmd += "-ca {}{} ".format(CRT_DST_PATH, GNMI_CA)
    cmd += "-logtostderr -rpc {} ".format(rpc)
    cmd += f'-jsonin \'{request_json_data}\''
    output = duthost.shell(cmd, module_ignore_errors=True)
    if output['stderr']:
        logger.error(output['stderr'])
        return -1, output['stderr']
    else:
        return 0, output['stdout']


def cert_date_on_dut(duthost):
    cmd = "openssl x509 -in {}{} -text".format(CRT_DST_PATH, GNMI_CA)
    output = duthost.shell(cmd, module_ignore_errors=True)
    not_after_line = re.search(r"Not After\s*:\s*(.*)", output['stdout'])
    if not_after_line:
        not_after_date_str = not_after_line.group(1).strip()
        # Convert the date string to a datetime object
        expiry_date = datetime.strptime(not_after_date_str, "%b %d %H:%M:%S %Y GMT")
        # comparison date is January 20, 2038, after the 2038 problem
        after_2038_problem_date = datetime(2038, 1, 20)

        if expiry_date < after_2038_problem_date:
            raise Exception("The expiry date {} is not after 2038 problem date".format(expiry_date))
        else:
            logger.info("The expiry date {} is after January 20, 2038.".format(expiry_date))
    else:
        raise Exception("The 'Not After' line with expiry date was not found")


def prepare_root_cert(localhost, days="1825"):
    create_root_key(localhost)
    create_root_cert(localhost, days)


def create_root_key(localhost):
    local_command = "openssl genrsa -out {} 2048".format(GNMI_CA_KEY)
    localhost.shell(local_command)


def create_root_cert(localhost, days):
    local_command = "openssl req \
                            -x509 \
                            -new \
                            -nodes \
                            -key {} \
                            -sha256 \
                            -days {} \
                            -subj '/CN=test.gnmi.sonic' \
                            -out {}".format(GNMI_CA_KEY, days, GNMI_CA)
    localhost.shell(local_command)


def prepare_server_cert(duthost, localhost, days="825"):
    create_server_key(localhost)
    create_server_csr(localhost)
    sign_server_certificate(duthost, localhost, days)


def create_server_key(localhost):
    local_command = "openssl genrsa -out {} 2048".format(SERVER_KEY)
    localhost.shell(local_command)


def create_server_csr(localhost):
    local_command = "openssl req \
                            -new \
                            -key {} \
                            -subj '/CN=test.server.gnmi.sonic' \
                            -out {}".format(SERVER_KEY, SERVER_CSR)
    localhost.shell(local_command)


def sign_server_certificate(duthost, localhost, days):
    create_ext_conf(duthost.mgmt_ip, "extfile.cnf")
    local_command = "openssl x509 \
                            -req \
                            -in {} \
                            -CA {} \
                            -CAkey {} \
                            -CAcreateserial \
                            -out {} \
                            -days {} \
                            -sha256 \
                            -extensions req_ext \
                            -extfile extfile.cnf".format(SERVER_CSR, GNMI_CA, GNMI_CA_KEY, SERVER_CRT, days)
    localhost.shell(local_command)


def prepare_client_cert(localhost, days="825"):
    create_client_key(localhost)
    create_client_csr(localhost)
    sign_client_certificate(localhost, days)


def create_client_key(localhost):
    local_command = "openssl genrsa -out {} 2048".format(CLIENT_KEY)
    localhost.shell(local_command)


def create_client_csr(localhost):
    local_command = "openssl req \
                            -new \
                            -key {} \
                            -subj '/CN=test.client.gnmi.sonic' \
                            -out {}".format(CLIENT_KEY, CLIENT_CSR)
    localhost.shell(local_command)


def sign_client_certificate(localhost, days):
    local_command = "openssl x509 \
                            -req \
                            -in {} \
                            -CA {} \
                            -CAkey {} \
                            -CAcreateserial \
                            -out {} \
                            -days {} \
                            -sha256".format(CLIENT_CSR, GNMI_CA, GNMI_CA_KEY, CLIENT_CRT, days)
    localhost.shell(local_command)


def copy_certificate_to_dut(duthost):
    # Copy CA certificate, server certificate and client certificate over to the DUT
    duthost.copy(src=GNMI_CA, dest=CRT_DST_PATH)
    duthost.copy(src=SERVER_CRT, dest=CRT_DST_PATH)
    duthost.copy(src=SERVER_KEY, dest=CRT_DST_PATH)
    duthost.copy(src=CLIENT_CRT, dest=CRT_DST_PATH)
    duthost.copy(src=CLIENT_KEY, dest=CRT_DST_PATH)


def copy_certificate_to_ptf(ptfhost):
    # Copy CA certificate and client certificate over to the PTF
    ptfhost.copy(src=GNMI_CA, dest='/root/')
    ptfhost.copy(src=CLIENT_CRT, dest='/root/')
    ptfhost.copy(src=CLIENT_KEY, dest='/root/')
