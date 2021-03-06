#!/bin/env python
# vim: expandtab:tabstop=4:shiftwidth=4

""" Listen for kubernetes PLEG creation events, and launch an
    image-inspector scan for every newly created customer container. """

import select
import subprocess
import re

from Queue import Queue
from threading import Thread
from systemd import journal


class PlegEventListener(object):
    """ Class to receive and report scan results. """


    def scan_worker(self, scan_q):
        """ Worker thread function. """

        while True:
            container = scan_q.get()
            self.process_container(container)
            scan_q.task_done()


    @staticmethod
    def catch_creates(scan_q):
        """ Watch the host node journal for creates. """

        j = journal.Reader(path='/host/run/log/journal')

        j.log_level(journal.LOG_INFO)

        j.this_boot()

        j.add_match(
            _SYSTEMD_UNIT=u'atomic-openshift-node.service',
        )

        j.seek_tail()

        j.get_previous()

        pollobj = select.poll()

        journal_fd = j.fileno()
        poll_event_mask = j.get_events()
        pollobj.register(journal_fd, poll_event_mask)

        # declare some queue here
        while True:
            if pollobj.poll(10000):
                if j.process() == journal.APPEND:
                    for entry in j:
                        match = re.search(r"(&pleg\.PodLifecycleEvent).+(ContainerStarted)", \
                                entry['MESSAGE'], re.IGNORECASE)
                        if match:
                            container_id = entry['MESSAGE'].split('Data:')[1].split('"')[1::2][0]

                            scan_q.put(container_id)


    @staticmethod
    def process_container(container_id):
        """ Check if provided container should be scanned. """

        inspect_output = subprocess.check_output([\
        'chroot', \
        '/host', \
        '/usr/bin/docker', \
        'inspect', \
        '--format', \
        '\'{{.Name}} \
        {{index .Config.Labels "io.kubernetes.pod.namespace"}} \'', \
        container_id
                                                 ])

        container_name = inspect_output.split()[0]
        container_ns = inspect_output.split()[1]

        is_pod = re.match(r"(^\/k8s_POD)", container_name, re.I)
        is_ops = re.match(r"(^openshift-)\w+", container_ns, re.I)

        scan_cmd = ['image-inspector', '-scan-type=clamav', '-clam-socket=/host/host/var/run/clamd.scan/clamd.sock', '-container=' + container_id, '-post-results-url=http://localhost:8080']

        if not is_pod or not is_ops:
            subprocess.call(scan_cmd)


    def main(self):
        """ Main function. """

        scan_q = Queue()
        worker = Thread(target=self.scan_worker, args=(scan_q,))
        worker.setDaemon(True)
        worker.start()

        self.catch_creates(scan_q)


if __name__ == '__main__':
    EVENTLISTENER = PlegEventListener()
    EVENTLISTENER.main()
