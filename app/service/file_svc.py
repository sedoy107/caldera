import os
import uuid

from aiohttp import web

from app.service.base_service import BaseService
from app.utility.payload_encoder import xor_file


class FileSvc(BaseService):

    def __init__(self, plugins, exfil_dir):
        self.plugins = plugins
        self.exfil_dir = exfil_dir
        self.log = self.add_service('file_svc', self)
        self.data_svc = self.get_service('data_svc')
        self.special_payloads = dict()

    async def download(self, request):
        """
        Accept a request with a required header, file, and an optional header, platform, and download the file.
        :param request:
        :return: a multipart file via HTTP
        """
        try:
            payload = request.headers.get('file')
            if payload in self.special_payloads:
                payload = await self.special_payloads[payload](request.headers)
            payload, content = await self.read_file(payload)
            headers = dict([('CONTENT-DISPOSITION', 'attachment; filename="%s"' % payload)])
            return web.Response(body=content, headers=headers)
        except FileNotFoundError:
            return web.HTTPNotFound(body='File not found')
        except Exception as e:
            return web.HTTPNotFound(body=e)

    async def upload(self, request):
        """
        Accept a multipart file via HTTP and save it to the server
        :param request:
        :return: None
        """
        try:
            reader = await request.multipart()
            exfil_dir = await self._create_exfil_sub_directory(request.headers)
            while True:
                field = await reader.next()
                if not field:
                    break
                filename = field.filename
                with open(os.path.join(exfil_dir, filename), 'wb') as f:
                    while True:
                        chunk = await field.read_chunk()
                        if not chunk:
                            break
                        f.write(chunk)
                self.log.debug('Uploaded file %s' % filename)
            return web.Response()
        except Exception as e:
            self.log.debug('Exception uploading file %s' % e)

    async def find_file_path(self, name, location=''):
        """
        Find the location on disk of a file by name.
        :param name:
        :param location:
        :return: a tuple: the plugin the file is found in & the relative file path
        """
        for plugin in self.plugins:
            file_path = await self._walk_file_path('plugins/%s/%s' % (plugin, location), name)
            if file_path:
                return plugin, file_path
        return None, await self._walk_file_path('%s' % location, name)

    async def read_file(self, name):
        """
        Open a file and read the contents
        :param name:
        :return: a tuple (file_path, contents)
        """
        for loc in ['payloads', 'data']:
            _, file_name = await self.find_file_path(name, location=loc)
            if file_name:
                with open(file_name, 'rb') as file_stream:
                    return name, file_stream.read()
            _, file_name = await self.find_file_path('%s.xored' % (name,), location=loc)
            if file_name:
                return name, xor_file(file_name)
        raise FileNotFoundError

    async def save_file(self, name, content, xored=False):
        """
        Save a (payload) file to the stockpile
        :param name: filename
        :param content: content to save
        :param xored: whether or not to xor the contents when saving
        :return: full path of the saved file
        """
        filebase = 'data/payloads/'
        f_content = content
        filename = str(os.path.join('a',name).split(os.path.sep)[-1])
        if xored:
            filename = filename + 'xored'
            f_content = xor_file(content)
        with open(os.path.join(filebase, filename), 'xb') as file:
            file.write(f_content)
        return filename

    async def add_special_payload(self, name, func):
        """
        Call a special function when specific payloads are downloaded
        :param name:
        :param func:
        :return:
        """
        self.special_payloads[name] = func

    async def find_payloads(self):
        """
        Identify the full gamut of available payloads (filtering out adversary mode)
        :return: list of available payloads
        """
        listing = []
        for plugin in self.plugins:
            for root, _, files in os.walk('plugins/%s' % plugin):
                if root.endswith('payloads') and 'adversary' not in root:
                    listing.extend(files)
            for _, _, files in os.walk('data/payloads'):
                for file in files:
                    if not file.endswith('.gitkeep'):
                        listing.append(file)
        return listing

    @staticmethod
    async def compile_go(platform, output, src_fle, ldflags='-s -w'):
        """
        Dynamically compile a go file
        :param platform:
        :param output:
        :param src_fle:
        :param ldflags: A string of ldflags to use when building the go executable
        :return:
        """
        os.system('GOOS=%s go build -o %s -ldflags="%s" %s' % (platform, output, ldflags, src_fle))

    """ PRIVATE """

    async def _walk_file_path(self, path, target):
        for root, dirs, files in os.walk(path):
            if target in files:
                self.log.debug('Located %s' % target)
                return os.path.join(root, target)
        return None

    async def _create_exfil_sub_directory(self, headers):
        dir_name = headers.get('X-Request-ID', str(uuid.uuid4()))
        path = os.path.join(self.exfil_dir, dir_name)
        if not os.path.exists(path):
            os.makedirs(path)
        return path
