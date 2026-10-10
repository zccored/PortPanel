import os
import logging
import threading
import time
import websockets
import aiohttp
import asyncio
from PySide6.QtCore import QObject, Signal, QTimer,QThread
from typing import Optional, Dict, Any
import json
import hashlib
from datetime import datetime
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

import http.server
import socketserver
import urllib.parse
import tempfile
import requests
from typing import Optional, Dict, Any

# 阿里云 OSS SDK
try:
    import oss2
    HAS_ALIYUN_OSS = True
except ImportError:
    HAS_ALIYUN_OSS = False
    print("警告: 未安装阿里云OSS SDK，请运行: pip install oss2")

try:
    import cryptography
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False

# 阿里云 ECS SDK
try:
    from aliyunsdkcore.client import AcsClient
    from aliyunsdkecs.request.v20140526 import DescribeInstancesRequest
    HAS_ALIYUN_ECS = True
except ImportError:
    HAS_ALIYUN_ECS = False
    print("警告: 未安装阿里云ECS SDK，请运行: pip install aliyun-python-sdk-ecs")

class ConfigEncryptor:
    """配置文件加密解密工具类"""
    
    def __init__(self, password: str = None):
        """
        初始化加密器
        
        Args:
            password: 加密密码，如果为None则使用默认密码
        """
        self.password = password or self._get_default_password()
        self.salt = b'aliyun_config_salt_'  # 固定的盐值
        # 设置日志
        self.logger = logging.getLogger('aliyun_client')
        
    def _get_default_password(self) -> str:
        """获取默认密码（基于机器特征）"""
        try:
            # 使用机器名和用户名生成默认密码
            import socket
            import getpass
            machine_info = f"{socket.gethostname()}_{getpass.getuser()}_aliyun_config"
            return hashlib.md5(machine_info.encode()).hexdigest()[:32]
        except:
            # 如果获取失败，使用固定密码
            return "default_aliyun_config_password_2024"
    
    def _derive_key(self) -> bytes:
        """从密码派生密钥"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.password.encode()))
        return key
    
    def encrypt_data(self, data: str) -> str:
        """加密数据"""
        try:
            key = self._derive_key()
            fernet = Fernet(key)
            encrypted_data = fernet.encrypt(data.encode())
            return base64.urlsafe_b64encode(encrypted_data).decode()
        except Exception as e:
            logging.error(f"数据加密失败: {str(e)}")
            raise
    
    def decrypt_data(self, encrypted_data: str) -> str:
        """解密数据"""
        try:
            key = self._derive_key()
            fernet = Fernet(key)
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_data.encode())
            decrypted_data = fernet.decrypt(encrypted_bytes)
            return decrypted_data.decode()
        except Exception as e:
            logging.error(f"数据解密失败: {str(e)}")
            raise
    
    def encrypt_config_file(self, input_file: str, output_file: str = None) -> bool:
        """加密配置文件"""
        if output_file is None:
            output_file = input_file
            
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                config_data = f.read()
            
            encrypted_data = self.encrypt_data(config_data)
            
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(encrypted_data)
                
            self.logger.info(f"配置文件加密成功: {input_file} -> {output_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"配置文件加密失败: {str(e)}")
            return False
    
    def decrypt_config_file(self, input_file: str, output_file: str = None) -> bool:
        """解密配置文件"""
        if output_file is None:
            output_file = input_file
            
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                encrypted_data = f.read()
            
            decrypted_data = self.decrypt_data(encrypted_data)
            
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(decrypted_data)
                
            self.logger.info(f"配置文件解密成功: {input_file} -> {output_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"配置文件解密失败: {str(e)}")
            return False

class AliyunClient:
    """
    阿里云客户端类，用于管理阿里云服务连接
    """
    
    def __init__(self):
        self.oss_client = None
        self.ecs_client = None
        self.connected = False
        self.connection_status = "未连接"
        self.last_connection_test = None
        
        # 配置信息
        self.config = {
            'access_key_id': '',
            'access_key_secret': '',
            'endpoint': 'oss-cn-hangzhou.aliyuncs.com',  # 默认端点
            'bucket_name': '',
            'region_id': 'cn-hangzhou'  # 默认区域
        }
        
        # 设置日志
        self.logger = logging.getLogger('aliyun_client')
        
    def set_config(self, access_key_id: str, access_key_secret: str, 
                   endpoint: str = None, bucket_name: str = None, 
                   region_id: str = None):
        """
        设置阿里云配置
        """
        self.config['access_key_id'] = access_key_id
        self.config['access_key_secret'] = access_key_secret
        
        if endpoint:
            self.config['endpoint'] = endpoint
        if bucket_name:
            self.config['bucket_name'] = bucket_name
        if region_id:
            self.config['region_id'] = region_id
            
        self.logger.info("阿里云配置已更新")
    
    def initialize_oss_client(self) -> bool:
        """
        初始化OSS客户端
        """
        if not HAS_ALIYUN_OSS:
            self.logger.error("OSS SDK未安装，无法初始化OSS客户端")
            return False
            
        try:
            auth = oss2.Auth(self.config['access_key_id'], self.config['access_key_secret'])
            self.oss_client = oss2.Bucket(auth, self.config['endpoint'], self.config['bucket_name'])
            self.logger.info(f"OSS客户端初始化成功 - 端点: {self.config['endpoint']}, 存储桶: {self.config['bucket_name']}")
            return True
        except Exception as e:
            self.logger.error(f"OSS客户端初始化失败: {str(e)}")
            return False
    
    def initialize_ecs_client(self) -> bool:    #26.08.27，增加了对key的初始化，这边加了一条验证
        """
        初始化ECS客户端
        
        Returns:
            bool: 初始化是否成功
        """
        if not HAS_ALIYUN_ECS:
            self.logger.error("ECS SDK未安装，无法初始化ECS客户端")
            return False
            
        try:
            self.ecs_client = AcsClient(
                self.config['access_key_id'], 
                self.config['access_key_secret'], 
                self.config['region_id']
            )
            self.logger.info(f"ECS客户端初始化成功 - 区域: {self.config['region_id']}")
            return True
        except Exception as e:
            self.logger.error(f"ECS客户端初始化失败: {str(e)}")
            return False
        
    def download_file(self, remote_path: str, local_path: str) -> Dict[str, Any]:   #26.08.25，预留对象存储的下载功能，返回字典，包含成功与否和信息
        """
        从OSS下载文件到本地
        
        Args:
            remote_path: OSS上的文件路径
            local_path: 本地保存路径
            
        Returns:
            Dict: 下载结果
        """
        if not self.oss_client:
            return {'success': False, 'message': 'OSS客户端未初始化'}
            
        try:
            # 确保本地目录存在
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            # 下载文件
            self.oss_client.get_object_to_file(remote_path, local_path)
            
            self.logger.info(f"文件下载成功: {remote_path} -> {local_path}")
            return {
                'success': True,
                'message': '文件下载成功',
                'remote_path': remote_path,
                'local_path': local_path
            }
            
        except oss2.exceptions.NoSuchKey:
            return {
                'success': False,
                'message': f'OSS文件不存在: {remote_path}'
            }
        except Exception as e:
            error_msg = f"文件下载失败: {str(e)}"
            self.logger.error(error_msg)
            return {
                'success': False,
                'message': error_msg
            }
        
    def upload_file(self, local_path: str, remote_path: str) -> Dict[str, Any]:
        """
        上传本地文件到OSS
        
        Args:
            local_path: 本地文件路径
            remote_path: OSS上的保存路径
            
        Returns:
            Dict: 上传结果
        """
        if not self.oss_client:
            return {'success': False, 'message': 'OSS客户端未初始化'}
            
        if not os.path.exists(local_path):
            return {'success': False, 'message': f'本地文件不存在: {local_path}'}
            
        try:
            # 上传文件
            result = self.oss_client.put_object_from_file(remote_path, local_path)
            
            if result.status == 200:
                self.logger.info(f"文件上传成功: {local_path} -> {remote_path}")
                return {
                    'success': True,
                    'message': '文件上传成功',
                    'local_path': local_path,
                    'remote_path': remote_path,
                    'etag': result.etag
                }
            else:
                return {
                    'success': False,
                    'message': f'上传失败，状态码: {result.status}'
                }
                
        except Exception as e:
            error_msg = f"文件上传失败: {str(e)}"
            self.logger.error(error_msg)
            return {
                'success': False,
                'message': error_msg
            }
    
    def upload_bytes(self, remote_path: str, data: bytes) -> Dict[str, Any]:
        """以内存字节上传到OSS（不产生本地临时文件）。

        用于敏感文件：调用方负责先加密为密文再上传。
        """
        if not self.oss_client:
            return {'success': False, 'message': 'OSS客户端未初始化'}
        try:
            result = self.oss_client.put_object(remote_path, data)
            if result.status == 200:
                self.logger.info(f"字节上传成功: {remote_path}")
                return {
                    'success': True,
                    'message': '上传成功',
                    'remote_path': remote_path,
                    'etag': result.etag,
                    'size': len(data),
                }
            return {'success': False, 'message': f'上传失败，状态码: {result.status}'}
        except Exception as e:
            self.logger.error(f"字节上传失败: {str(e)}")
            return {'success': False, 'message': f"上传失败: {str(e)}"}

    def download_bytes(self, remote_path: str) -> Dict[str, Any]:
        """从OSS下载到内存字节（不产生本地临时文件）。

        返回 {'success': True, 'data': bytes, ...}；敏感文件由调用方在内存解密。
        """
        if not self.oss_client:
            return {'success': False, 'message': 'OSS客户端未初始化'}
        try:
            obj = self.oss_client.get_object(remote_path)
            data = obj.read()
            return {
                'success': True,
                'data': data,
                'remote_path': remote_path,
                'size': len(data),
            }
        except oss2.exceptions.NoSuchKey:
            return {'success': False, 'message': f'OSS文件不存在: {remote_path}'}
        except Exception as e:
            self.logger.error(f"字节下载失败: {str(e)}")
            return {'success': False, 'message': f"下载失败: {str(e)}"}

    def get_file_info(self, remote_path: str) -> Dict[str, Any]:
        """
        获取OSS文件信息（包括最后修改时间）
        
        Args:
            remote_path: OSS文件路径
            
        Returns:
            Dict: 文件信息
        """
        if not self.oss_client:
            return {'success': False, 'message': 'OSS客户端未初始化'}
            
        try:
            # 获取文件元信息 - 修复方法
            result = self.oss_client.head_object(remote_path)
            
            # 从结果中提取头部信息
            headers = {
                'last-modified': result.headers.get('Last-Modified', ''),
                'content-length': result.headers.get('Content-Length', '0'),
                'etag': result.headers.get('ETag', '')
            }
            
            # 提取最后修改时间
            last_modified = headers['last-modified']
            content_length = headers['content-length']
            etag = headers['etag']
            
            file_info = {
                'success': True,
                'last_modified': last_modified,
                'size': int(content_length) if content_length.isdigit() else 0,
                'etag': etag,
                'exists': True
            }
            
            # 尝试解析时间
            try:
                # 解析OSS返回的时间格式: "Sun, 26 Oct 2025 17:02:44 GMT"
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(last_modified)
                file_info['last_modified_timestamp'] = dt.timestamp()
                file_info['last_modified_datetime'] = dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception as time_error:
                self.logger.warning(f"解析时间失败 {last_modified}: {str(time_error)}")
                file_info['last_modified_timestamp'] = 0
                file_info['last_modified_datetime'] = last_modified
                
            return file_info
            
        except oss2.exceptions.NoSuchKey:
            return {
                'success': True,
                'exists': False,
                'message': f'文件不存在: {remote_path}'
            }
        except Exception as e:
            error_msg = f"获取文件信息失败: {str(e)}"
            self.logger.error(error_msg)
            return {
                'success': False,
                'message': error_msg
            }
    
    def list_remote_files(self, prefix: str = '') -> Dict[str, Any]:
        """
        列出OSS上的文件
        
        Args:
            prefix: 文件前缀过滤
            
        Returns:
            Dict: 文件列表
        """
        if not self.oss_client:
            return {'success': False, 'message': 'OSS客户端未初始化'}
            
        try:
            files = []
            for obj in oss2.ObjectIterator(self.oss_client, prefix=prefix):
                files.append({
                    'key': obj.key,
                    'last_modified': obj.last_modified,
                    'size': obj.size,
                    'etag': obj.etag
                })
            
            return {
                'success': True,
                'files': files,
                'count': len(files)
            }
            
        except Exception as e:
            error_msg = f"列出文件失败: {str(e)}"
            self.logger.error(error_msg)
            return {
                'success': False,
                'message': error_msg
            }
    
    def delete_remote_file(self, remote_path: str) -> Dict[str, Any]:
        """
        删除OSS上的文件
        
        Args:
            remote_path: OSS文件路径
            
        Returns:
            Dict: 删除结果
        """
        if not self.oss_client:
            return {'success': False, 'message': 'OSS客户端未初始化'}
            
        try:
            self.oss_client.delete_object(remote_path)
            
            self.logger.info(f"OSS文件删除成功: {remote_path}")
            return {
                'success': True,
                'message': '文件删除成功',
                'remote_path': remote_path
            }
            
        except Exception as e:
            error_msg = f"文件删除失败: {str(e)}"
            self.logger.error(error_msg)
            return {
                'success': False,
                'message': error_msg
            }

    def calculate_file_hash(self, file_path: str) -> str:
        """
        计算文件的MD5哈希值
        
        Args:
            file_path: 文件路径
            
        Returns:
            str: MD5哈希值
        """
        if not os.path.exists(file_path):
            return ""
            
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            self.logger.error(f"计算文件哈希失败 {file_path}: {str(e)}")
            return ""

    def compare_files(self, local_path: str, remote_path: str) -> Dict[str, Any]:
        """
        比较本地文件和远程文件的差异 - 修复版本
        
        Args:
            local_path: 本地文件路径
            remote_path: 远程文件路径
            
        Returns:
            Dict: 比较结果
        """
        result = {
            'local_exists': os.path.exists(local_path),
            'remote_exists': False,
            'needs_sync': False,
            'sync_type': None,  # 'upload', 'download', 'conflict'
            'differences': [],
            'local_mtime': 0,
            'remote_mtime': 0,
            'local_size': 0,
            'remote_size': 0,
            'local_mtime_str': '',
            'remote_mtime_str': ''
        }
        
        # 检查本地文件信息
        if result['local_exists']:
            local_stat = os.stat(local_path)
            result['local_size'] = local_stat.st_size
            result['local_mtime'] = local_stat.st_mtime
            result['local_hash'] = self.calculate_file_hash(local_path)
            result['local_mtime_str'] = datetime.fromtimestamp(local_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            self.logger.info(f"本地文件信息: 大小={result['local_size']}, 修改时间={result['local_mtime_str']}")
        
        # 检查远程文件信息
        remote_info = self.get_file_info(remote_path)
        self.logger.info(f"远程文件检查结果: {remote_info}")
        
        if remote_info['success'] and remote_info.get('exists', False):
            result['remote_exists'] = True
            result['remote_size'] = remote_info['size']
            result['remote_mtime'] = remote_info.get('last_modified_timestamp', 0)
            result['remote_mtime_str'] = remote_info.get('last_modified_datetime', '')
            result['remote_etag'] = remote_info.get('etag', '')
            self.logger.info(f"远程文件信息: 大小={result['remote_size']}, 修改时间={result['remote_mtime_str']}")
        
        # 判断是否需要同步
        if not result['local_exists'] and result['remote_exists']:
            # 只有远程文件存在，需要下载
            result['needs_sync'] = True
            result['sync_type'] = 'download'
            result['differences'].append('本地文件不存在，需要从云端下载')
            self.logger.info(f"需要下载: 本地不存在, 远程存在")
            
        elif result['local_exists'] and not result['remote_exists']:
            # 只有本地文件存在，需要上传
            result['needs_sync'] = True
            result['sync_type'] = 'upload'
            result['differences'].append('云端文件不存在，需要上传到云端')
            self.logger.info(f"需要上传: 本地存在, 远程不存在")
            
        elif result['local_exists'] and result['remote_exists']:
            # 两边都存在，比较差异
            differences = []
            
            # 比较文件大小
            if result['local_size'] != result['remote_size']:
                differences.append(f'文件大小不同: 本地{result["local_size"]}字节, 云端{result["remote_size"]}字节')
                self.logger.info(f"文件大小不同: 本地{result['local_size']} vs 远程{result['remote_size']}")
            
            # 比较修改时间（放宽到5分钟误差）
            time_diff = abs(result['local_mtime'] - result['remote_mtime'])
            if time_diff > 300:  # 5分钟误差
                differences.append(f'修改时间不同: 本地{result["local_mtime_str"]}, 云端{result["remote_mtime_str"]}')
                self.logger.info(f"修改时间不同: 本地{result['local_mtime_str']} vs 远程{result['remote_mtime_str']}, 差异={time_diff}秒")
            
            # 如果有差异，需要同步
            if differences:
                result['needs_sync'] = True
                result['sync_type'] = 'conflict'
                result['differences'] = differences
                self.logger.info(f"文件需要同步，差异: {differences}")
            else:
                result['needs_sync'] = False
                result['differences'].append('文件完全相同，无需同步')
                self.logger.info("文件完全相同，无需同步")
        
        return result
    
    def test_connection(self) -> Dict[str, Any]:
        """
        测试阿里云连接 - 修复返回值逻辑
        """
        self.last_connection_test = time.time()
        results = {
            'success': False,
            'oss_connected': False,
            'ecs_connected': False,
            'oss_service_available': True,
            'message': '',
            'timestamp': self.last_connection_test
        }
        
        # 测试OSS连接
        oss_success = False
        oss_error_message = ""
        if HAS_ALIYUN_OSS and self.oss_client:
            try:
                # 尝试列出对象（限制1个来测试连接）
                for obj in oss2.ObjectIterator(self.oss_client, max_keys=1):
                    oss_success = True
                    break
                # 如果没有任何对象，也会成功
                oss_success = True
                results['oss_connected'] = True
                self.logger.info("OSS连接测试成功")
            except oss2.exceptions.NoSuchBucket:
                # 存储桶不存在
                oss_error_message = "存储桶不存在，请检查存储桶名称"
                results['oss_service_available'] = True
            except oss2.exceptions.AccessDenied:
                # 权限被拒绝
                oss_error_message = "访问被拒绝，请检查权限设置"
                results['oss_service_available'] = True
            except oss2.exceptions.ServerError as e:
                # 服务器错误，检查是否是服务未开通
                error_msg = str(e).lower()
                if "未开通" in error_msg or "not activated" in error_msg:
                    oss_error_message = "对象存储服务(OSS)未开通"
                    results['oss_service_available'] = False
                else:
                    oss_error_message = f"OSS服务器错误: {str(e)}"
                    results['oss_service_available'] = True
            except oss2.exceptions.ClientError as e:
                # 客户端错误
                error_msg = str(e).lower()
                if "invalid access key" in error_msg:
                    oss_error_message = "AccessKey无效"
                elif "signature" in error_msg:
                    oss_error_message = "签名错误，请检查AccessKey Secret"
                else:
                    oss_error_message = f"OSS客户端错误: {str(e)}"
                results['oss_service_available'] = True
            except Exception as e:
                oss_error_message = f"OSS连接测试失败: {str(e)}"
                results['oss_service_available'] = True
            
            if oss_error_message:
                self.logger.error(f"OSS连接测试失败: {oss_error_message}")
                results['message'] += f"OSS: {oss_error_message}; "
        
        # 测试ECS连接
        ecs_success = False
        if HAS_ALIYUN_ECS and self.ecs_client:
            try:
                request = DescribeInstancesRequest.DescribeInstancesRequest()
                request.set_accept_format('json')
                request.set_PageSize(1)
                
                response = self.ecs_client.do_action_with_exception(request)
                ecs_success = True
                results['ecs_connected'] = True
                self.logger.info("ECS连接测试成功")
            except Exception as e:
                error_msg = str(e)
                self.logger.error(f"ECS连接测试失败: {error_msg}")
                results['message'] += f"ECS连接失败: {error_msg}; "
        
        # 修复总体结果判断逻辑
        # 如果OSS连接成功或ECS连接成功，就认为是成功的
        if oss_success or ecs_success:
            results['success'] = True
            if oss_success and ecs_success:
                results['message'] = "阿里云连接测试成功 - OSS和ECS服务均正常"
            elif oss_success and not ecs_success:
                results['message'] = "OSS连接成功，ECS连接失败"
            elif not oss_success and ecs_success:
                results['message'] = f"ECS连接成功，但OSS连接失败: {oss_error_message}"
            
            self.connected = True
            self.connection_status = "已连接"
        else:
            results['message'] = "所有阿里云服务连接测试失败" if results['message'] == '' else results['message']
            self.connected = False
            self.connection_status = "连接失败"
            
        return results

    # 修改 send_custom_bytes 方法来处理OSS未开通的情况
    def send_custom_bytes(self, data: bytes, object_key: str = "connection_test.bin") -> Dict[str, Any]:
        """
        发送自定义字节数据到OSS（用于连接验证）
        """
        if not self.oss_client:
            return {'success': False, 'message': 'OSS客户端未初始化'}
            
        try:
            # 上传字节数据
            result = self.oss_client.put_object(object_key, data)
            
            if result.status == 200:
                self.logger.info(f"自定义字节数据上传成功: {object_key}, 大小: {len(data)} 字节")
                return {
                    'success': True,
                    'message': '数据上传成功',
                    'object_key': object_key,
                    'size': len(data),
                    'etag': result.etag
                }
            else:
                self.logger.error(f"数据上传失败，状态码: {result.status}")
                return {
                    'success': False,
                    'message': f'上传失败，状态码: {result.status}'
                }
                
        except oss2.exceptions.ServerError as e:
            error_msg = str(e)
            if "未开通" in error_msg or "not activated" in error_msg:
                return {
                    'success': False,
                    'message': '对象存储服务(OSS)未开通，无法上传验证数据'
                }
            else:
                return {
                    'success': False,
                    'message': f'OSS服务器错误: {error_msg}'
                }
        except Exception as e:
            error_msg = f"数据上传异常: {str(e)}"
            self.logger.error(error_msg)
            return {
                'success': False,
                'message': error_msg
            }
    
    def list_ecs_instances(self) -> Dict[str, Any]:
        """
        列出ECS实例
        
        Returns:
            Dict: ECS实例列表
        """
        if not self.ecs_client:
            return {'success': False, 'message': 'ECS客户端未初始化'}
            
        try:
            request = DescribeInstancesRequest.DescribeInstancesRequest()
            request.set_accept_format('json')
            request.set_PageSize(100)  # 最多100个实例
            
            response = self.ecs_client.do_action_with_exception(request)
            response_str = response.decode('utf-8')
            
            # 这里可以解析响应获取实例信息
            # 简化处理，只返回原始响应
            self.logger.info("成功获取ECS实例列表")
            return {
                'success': True,
                'message': 'ECS实例列表获取成功',
                'data': response_str
            }
            
        except Exception as e:
            error_msg = f"获取ECS实例列表失败: {str(e)}"
            self.logger.error(error_msg)
            return {
                'success': False,
                'message': error_msg
            }
    
    def get_connection_status(self) -> Dict[str, Any]:
        """
        获取连接状态
        
        Returns:
            Dict: 连接状态信息
        """
        return {
            'connected': self.connected,
            'status': self.connection_status,
            'last_test': self.last_connection_test,
            'oss_available': HAS_ALIYUN_OSS,
            'ecs_available': HAS_ALIYUN_ECS,
            'oss_initialized': self.oss_client is not None,
            'ecs_initialized': self.ecs_client is not None
        }


# 创建全局阿里云客户端实例
aliyun_client = AliyunClient()

def get_global_aliyun_client() -> AliyunClient:
    """
    获取全局阿里云客户端实例
    """
    return aliyun_client

def sync_file_to_oss(local_path: str, remote_path: str) -> Dict[str, Any]:
    """同步文件到OSS（带初始化检查）"""
    if not ensure_oss_initialized():
        return {'success': False, 'message': 'OSS客户端未初始化'}
    
    client = get_global_aliyun_client()
    return client.upload_file(local_path, remote_path)

def sync_file_from_oss(remote_path: str, local_path: str) -> Dict[str, Any]:
    """从OSS同步文件到本地（带初始化检查）"""
    if not ensure_oss_initialized():
        return {'success': False, 'message': 'OSS客户端未初始化'}
    
    client = get_global_aliyun_client()
    return client.download_file(remote_path, local_path)


def upload_bytes_to_oss(remote_path: str, data: bytes) -> Dict[str, Any]:
    """以内存字节上传到OSS（带初始化检查）。敏感文件请先加密。"""
    if not ensure_oss_initialized():
        return {'success': False, 'message': 'OSS客户端未初始化'}
    client = get_global_aliyun_client()
    return client.upload_bytes(remote_path, data)


def download_bytes_from_oss(remote_path: str) -> Dict[str, Any]:
    """从OSS下载到内存字节（带初始化检查）。敏感文件请解密后使用。"""
    if not ensure_oss_initialized():
        return {'success': False, 'message': 'OSS客户端未初始化'}
    client = get_global_aliyun_client()
    return client.download_bytes(remote_path)

def ensure_oss_initialized():
    """
    确保OSS客户端已初始化 - 保持原有逻辑，只读取 OSS 配置
    """
    client = get_global_aliyun_client()
    
    # 如果已经初始化并且连接正常，直接返回
    if client.oss_client and client.connected:
        return True
    
    # 尝试从配置文件读取配置并初始化
    try:
        config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "./data/api_config.json")
        
        if not os.path.exists(config_file):
            client.logger.warning(f"OSS配置文件不存在: {config_file}")
            return False
            
        client.logger.info(f"读取OSS配置文件: {config_file}")
        
        # 使用原有的配置读取逻辑（保持加密解密）
        config = _load_config_with_fallback(config_file)
        
        if not config:
            client.logger.error("无法读取OSS配置文件，返回空配置")
            return False
        
        # 只读取阿里云 OSS 相关配置
        access_key_id = config.get('aliyun_access_key_id', '')
        access_key_secret = config.get('aliyun_access_key_secret', '')
        endpoint = config.get('aliyun_endpoint', 'oss-cn-hangzhou.aliyuncs.com')
        bucket_name = config.get('aliyun_bucket', '')
        region_id = config.get('aliyun_region', 'cn-hangzhou')
        
        client.logger.info(f"OSS配置读取: access_key_id={bool(access_key_id)}, endpoint={endpoint}")
        
        if access_key_id and access_key_secret:
            # 设置配置
            client.set_config(access_key_id, access_key_secret, endpoint, bucket_name, region_id)
            
            # 初始化OSS客户端
            if client.initialize_oss_client():
                # 测试连接
                result = client.test_connection()
                client.logger.info(f"OSS初始化结果: {result['success']}")
                return result['success']
            else:
                client.logger.error("OSS客户端初始化失败")
        else:
            client.logger.warning("阿里云OSS配置不完整")
    
    except Exception as e:
        client.logger.error(f"自动初始化OSS客户端失败: {e}")
    
    return False

def _load_config_with_fallback(config_file: str) -> dict:
    """使用回退机制加载配置（与api_config_dialog.py保持一致）"""
    if not os.path.exists(config_file):
        return {}
        
    try:
        # 读取文件内容
        with open(config_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        
        # 如果是空文件，返回空配置
        if not content:
            return {}
                
        # 检查是否是加密文件
        if _looks_like_encrypted(content):
            logging.info("检测到加密配置文件，尝试解密...")
            encryptor = ConfigEncryptor()
            if encryptor:
                try:
                    decrypted_content = encryptor.decrypt_data(content)
                    
                    # 验证解密内容是否是有效的JSON
                    if decrypted_content.strip():
                        config = json.loads(decrypted_content)
                        logging.info("成功解密并解析配置文件")
                        return config
                    else:
                        logging.error("解密后的内容为空")
                        return {}
                        
                except json.JSONDecodeError as e:
                    logging.error(f"解密内容不是有效的JSON: {str(e)}")
                    # 尝试修复JSON
                    return _handle_corrupted_config(decrypted_content)
                except Exception as e:
                    logging.error(f"配置文件解密失败: {str(e)}")
                    # 解密失败，尝试作为明文读取
                    return _load_plain_config(config_file)
            else:
                logging.warning("加密器不可用，尝试明文读取")
                return _load_plain_config(config_file)
        else:
            # 直接作为明文读取
            logging.info("检测到明文配置文件")
            return _load_plain_config(config_file)
            
    except Exception as e:
        logging.error(f"加载配置失败: {e}")
        return {}

def _load_plain_config(config_file: str) -> dict:
    """加载明文配置文件"""
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config
    except Exception as e:
        logging.error(f"加载明文配置失败: {e}")
        return {}

def _handle_corrupted_config(decrypted_content: str) -> dict:
    """处理损坏的配置文件"""
    logging.warning("配置文件可能已损坏，尝试修复...")
    
    try:
        # 尝试各种修复方法
        
        # 方法1: 去除BOM头
        if decrypted_content.startswith('\ufeff'):
            decrypted_content = decrypted_content[1:]
            logging.info("已去除BOM头")
        
        # 方法2: 检查是否是有效的JSON
        if decrypted_content.strip():
            # 尝试直接解析
            config = json.loads(decrypted_content)
            logging.info("修复成功，配置文件有效")
            return config
    except:
        pass
    
    # 如果修复失败，返回空配置
    logging.error("无法修复配置文件")
    return {}

# def _load_encrypted_config(config_file: str) -> dict:
#     """加载加密的配置文件 - 增强版本"""
#     if not HAS_CRYPTOGRAPHY:
#         logging.warning("加密库不可用，无法解密配置文件")
#         return {}
        
#     try:
#         encryptor = ConfigEncryptor()
#         with open(config_file, 'r', encoding='utf-8') as f:
#             encrypted_content = f.read().strip()
        
#         if not encrypted_content:
#             logging.warning("加密配置文件为空")
#             return {}
            
#         decrypted_content = encryptor.decrypt_data(encrypted_content)
        
#         if not decrypted_content.strip():
#             logging.error("解密后的内容为空")
#             return {}
            
#         return json.loads(decrypted_content)
#     except json.JSONDecodeError as e:
#         logging.error(f"解密内容不是有效的JSON: {str(e)}")
#         # 尝试创建默认配置
#         return {}
#     except Exception as e:
#         logging.error(f"解密配置文件失败: {str(e)}")
#         return {}

def _looks_like_encrypted(content: str) -> bool:
    """判断内容是否看起来像加密数据"""
    try:
        base64.b64decode(content)
        return len(content) > 50
    except:
        return False

def compare_local_remote_file(local_path: str, remote_path: str) -> Dict[str, Any]:
    """
    比较本地和远程文件的差异（带初始化检查）
    """
    # 确保OSS客户端已初始化
    if not ensure_oss_initialized():
        return {
            'success': False,
            'needs_sync': False,
            'message': 'OSS客户端未初始化，请先配置阿里云OSS服务',
            'local_exists': os.path.exists(local_path),
            'remote_exists': False
        }
    
    client = get_global_aliyun_client()
    return client.compare_files(local_path, remote_path)

def get_remote_file_info(remote_path: str) -> Dict[str, Any]:
    """获取远程文件信息（带初始化检查）"""
    if not ensure_oss_initialized():
        return {'success': False, 'message': 'OSS客户端未初始化'}
    
    client = get_global_aliyun_client()
    return client.get_file_info(remote_path)

def initialize_aliyun_services(access_key_id: str, access_key_secret: str, 
                              endpoint: str = None, bucket_name: str = None,
                              region_id: str = None) -> bool:
    """
    初始化阿里云服务
    
    Args:
        access_key_id: 访问密钥ID
        access_key_secret: 访问密钥
        endpoint: OSS端点
        bucket_name: 存储桶名称
        region_id: 区域ID
        
    Returns:
        bool: 初始化是否成功
    """
    client = get_global_aliyun_client()
    client.set_config(access_key_id, access_key_secret, endpoint, bucket_name, region_id)
    
    # 初始化OSS客户端
    oss_initialized = client.initialize_oss_client()
    
    # 初始化ECS客户端
    ecs_initialized = client.initialize_ecs_client()
    
    return oss_initialized or ecs_initialized

def test_aliyun_connection() -> Dict[str, Any]:
    """
    测试阿里云连接
    
    Returns:
        Dict: 连接测试结果
    """
    client = get_global_aliyun_client()
    return client.test_connection()

def send_verification_bytes(custom_data: bytes = None) -> Dict[str, Any]:
    """
    发送验证字节数据
    
    Args:
        custom_data: 自定义字节数据，如果为None则使用默认数据
        
    Returns:
        Dict: 发送结果
    """
    client = get_global_aliyun_client()
    
    if custom_data is None:
        # 创建包含时间戳和验证信息的默认数据
        timestamp = time.time()
        custom_data = f"Connection verification - Timestamp: {timestamp} - App: 全栈图库管理器".encode('utf-8')
    
    return client.send_custom_bytes(custom_data)


# QQ官方API常量
QQ_API_BASE = "https://api.q.qq.com"
QQ_WS_BASE = "wss://api.q.qq.com"

class QQOfficialAPI:
    """QQ官方API接口类"""
    
    def __init__(self, app_id: str, token: str, env_id: str = ""):
        self.app_id = app_id
        self.token = token
        self.env_id = env_id
        self.session = None
        self.logger = logging.getLogger('qq_official_api')
        
    async def init_session(self):
        """初始化会话"""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
            
    async def close_session(self):
        """关闭会话"""
        if self.session:
            await self.session.close()
            self.session = None
            
    def get_headers(self):
        """获取API请求头"""
        return {
            'Authorization': f'QQBot {self.token}',
            'Content-Type': 'application/json',
            'X-Union-Appid': self.app_id
        }
        
    async def validate_credentials(self) -> bool:
        """验证凭证有效性"""
        try:
            await self.init_session()
            url = f"{QQ_API_BASE}/users/@me"
            
            async with self.session.get(url, headers=self.get_headers()) as response:
                if response.status == 200:
                    data = await response.json()
                    self.logger.info(f"QQ官方API凭证验证成功: {data.get('username', '未知用户')}")
                    return True
                else:
                    self.logger.error(f"QQ官方API凭证验证失败: {response.status}")
                    return False
        except Exception as e:
            self.logger.error(f"QQ官方API凭证验证异常: {e}")
            return False
            
    async def get_user_info(self, user_id: str = "@me") -> Dict:
        """获取用户信息"""
        try:
            await self.init_session()
            url = f"{QQ_API_BASE}/users/{user_id}"
            
            async with self.session.get(url, headers=self.get_headers()) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    self.logger.error(f"获取用户信息失败: {response.status}")
                    return {}
        except Exception as e:
            self.logger.error(f"获取用户信息异常: {e}")
            return {}
            
    async def send_message(self, channel_id: str, content: str, message_type: int = 0) -> Dict:
        """发送消息到频道"""
        try:
            await self.init_session()
            url = f"{QQ_API_BASE}/channels/{channel_id}/messages"
            
            payload = {
                'content': content,
                'msg_type': message_type  # 0: 文本消息
            }
            
            async with self.session.post(url, headers=self.get_headers(), json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    self.logger.info(f"消息发送成功: {data.get('id', '未知消息ID')}")
                    return data
                else:
                    error_text = await response.text()
                    self.logger.error(f"消息发送失败: {response.status} - {error_text}")
                    return {'success': False, 'error': error_text}
        except Exception as e:
            self.logger.error(f"消息发送异常: {e}")
            return {'success': False, 'error': str(e)}
            
    async def get_channel_messages(self, channel_id: str, limit: int = 20) -> list[Dict]:
        """获取频道消息"""
        try:
            await self.init_session()
            url = f"{QQ_API_BASE}/channels/{channel_id}/messages?limit={limit}"
            
            async with self.session.get(url, headers=self.get_headers()) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('messages', [])
                else:
                    self.logger.error(f"获取频道消息失败: {response.status}")
                    return []
        except Exception as e:
            self.logger.error(f"获取频道消息异常: {e}")
            return []
            
    async def connect_websocket(self, callback_func):
        """连接WebSocket接收实时消息"""
        try:
            # QQ官方WebSocket连接
            ws_url = f"{QQ_WS_BASE}/websocket/{self.app_id}"
            
            async with websockets.connect(
                ws_url,
                extra_headers=self.get_headers(),
                ping_interval=20,
                ping_timeout=10
            ) as websocket:
                self.logger.info("QQ官方WebSocket连接成功")
                
                # 发送认证
                auth_payload = {
                    "op": 2,
                    "d": {
                        "token": self.token,
                        "intents": 513,  # 消息意图
                        "properties": {
                            "$os": "linux",
                            "$browser": "official_bot",
                            "$device": "official_bot"
                        }
                    }
                }
                await websocket.send(json.dumps(auth_payload))
                
                # 监听消息
                async for message in websocket:
                    try:
                        data = json.loads(message)
                        await callback_func(data)
                    except Exception as e:
                        self.logger.error(f"处理WebSocket消息异常: {e}")
                        
        except Exception as e:
            self.logger.error(f"WebSocket连接异常: {e}")
            raise

class AliyunProxyClient(QObject):
    """阿里云代理客户端 - 集成QQ官方API版本"""

    # 信号定义
    connection_status_changed = Signal(str)
    qq_message_received = Signal(dict)
    error_occurred = Signal(str)
    bot_registered = Signal(str, str)
    proxy_status_changed = Signal(dict)  # 新增：代理状态详细信号
    
    
    def __init__(self):
        super().__init__()
        # 确保在主线程中创建
        self.moveToThread(QThread.currentThread())
        
        # 连接相关
        self.proxy_url = None
        self.websocket = None
        self.session = None
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5
        
        # QQ官方API客户端
        self.qq_api_clients = {}  # bot_id -> QQOfficialAPI
        self.qq_websocket_tasks = {}  # bot_id -> asyncio.Task
        
        # 定时器
        self.reconnect_timer = QTimer()
        self.reconnect_timer.setSingleShot(True)
        self.reconnect_timer.timeout.connect(self.try_reconnect)
        
        self.heartbeat_timer = QTimer()
        self.heartbeat_timer.timeout.connect(self.send_heartbeat_safe)
        
        # 配置管理器
        from official_bot_platform.ui_components import BotConfigManager
        self.bot_config_manager = BotConfigManager()
        
        # 日志
        self.logger = logging.getLogger('aliyun_client')
        
        # 初始化
        self.ensure_main_thread_initialization()

    def ensure_main_thread_initialization(self):
        """确保在主线程中初始化"""
        try:
            # 检查当前线程
            if QThread.currentThread() != self.thread():
                # 如果不在主线程，延迟初始化
                QTimer.singleShot(0, self.delayed_timer_init)
            else:
                self.init_timers()
        except Exception as e:
            self.logger.warning(f"线程初始化警告: {e}")
            self.init_timers()

    def delayed_timer_init(self):
        """延迟初始化定时器"""
        try:
            self.init_timers()
        except Exception as e:
            self.logger.error(f"定时器初始化失败: {e}")

    def init_timers(self):
        """初始化定时器"""
        # 确保定时器对象在主线程中
        if not hasattr(self, 'reconnect_timer'):
            self.reconnect_timer = QTimer()
            self.reconnect_timer.setSingleShot(True)
            self.reconnect_timer.timeout.connect(self.try_reconnect)
            
        if not hasattr(self, 'heartbeat_timer'):
            self.heartbeat_timer = QTimer()
            self.heartbeat_timer.setSingleShot(False)
            self.heartbeat_timer.timeout.connect(self.send_heartbeat_safe)
        
        # 移动定时器到当前线程
        self.reconnect_timer.moveToThread(QThread.currentThread())
        self.heartbeat_timer.moveToThread(QThread.currentThread())

    async def get_proxy_config(self):
        """获取代理服务器配置 - 使用新的 Bot 配置"""
        try:
            config = self.bot_config_manager.load_config()
            
            proxy_url = config.get('proxy_server_url', '')
            enabled = config.get('proxy_enabled', True)
            
            logging.info(f"代理客户端配置 - proxy_url: '{proxy_url}', enabled: {enabled}")
            
            if not proxy_url:
                logging.error("代理配置中 proxy_server_url 为空")
                return None
                
            return {
                'proxy_url': proxy_url,
                'enabled': enabled
            }
                
        except Exception as e:
            logging.error(f"获取代理配置失败: {e}")
            return None
        
    async def init_session(self):
        """初始化aiohttp会话"""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
            
    async def connect_to_proxy(self, proxy_config=None):
        """连接到代理服务器"""
        try:
            # 确保在主线程中初始化
            self.ensure_main_thread_initialization()
            
            # 获取代理服务器配置
            if proxy_config is None:
                proxy_config = await self.get_proxy_config()
                
            if not proxy_config or not proxy_config.get('enabled', True):
                self.error_occurred.emit("代理功能未启用")
                self.proxy_status_changed.emit({
                    'connected': False,
                    'status': 'disabled',
                    'message': '代理功能未启用'
                })
                return False
                
            proxy_url = proxy_config.get('proxy_url')
            if not proxy_url:
                self.error_occurred.emit("代理服务器地址未配置")
                self.proxy_status_changed.emit({
                    'connected': False,
                    'status': 'not_configured',
                    'message': '代理服务器地址未配置'
                })
                return False
                
            self.proxy_url = proxy_url
            
            # 清理现有连接
            await self.close()
            
            await self.init_session()
            
            # 构建WebSocket URL
            ws_url = self.proxy_url.replace('http', 'ws') + '/ws'
            
            self.logger.info(f"尝试连接到代理服务器: {ws_url}")
            
            # 连接WebSocket
            try:
                self.websocket = await websockets.connect(
                    ws_url,
                    ping_interval=30,
                    ping_timeout=10
                )
            except Exception as e:
                error_msg = f"WebSocket连接失败: {str(e)}"
                self.logger.error(error_msg)
                self.error_occurred.emit(error_msg)
                self.proxy_status_changed.emit({
                    'connected': False,
                    'status': 'connection_failed',
                    'message': error_msg
                })
                await self.schedule_reconnect()
                return False
                
            self.is_connected = True
            self.reconnect_attempts = 0
            
            # 停止重连定时器
            if self.reconnect_timer.isActive():
                self.reconnect_timer.stop()
            
            self.logger.info("代理服务器连接成功")
            
            # 发送连接状态
            self.connection_status_changed.emit("connected")
            self.proxy_status_changed.emit({
                'connected': True,
                'status': 'connected',
                'proxy_url': self.proxy_url,
                'message': f'已连接到代理服务器: {self.proxy_url}'
            })
            
            # 启动消息监听
            asyncio.create_task(self.listen_messages())
            
            # 延迟启动心跳
            QTimer.singleShot(2000, self.start_heartbeat_safe)
            
            return True
            
        except Exception as e:
            error_msg = f"连接代理服务器失败: {str(e)}"
            logging.error(error_msg)
            self.error_occurred.emit(error_msg)
            self.proxy_status_changed.emit({
                'connected': False,
                'status': 'error',
                'message': error_msg
            })
            await self.schedule_reconnect()
            return False
        
    async def send_heartbeat_safe(self):
        """安全发送心跳"""
        if self.is_connected and self.websocket:
            try:
                await self.send_heartbeat()
            except Exception as e:
                logging.error(f"发送心跳失败: {e}")
                self.is_connected = False
                await self.schedule_reconnect()
        
    def start_heartbeat_safe(self):
        """安全启动心跳定时器"""
        try:
            if not self.heartbeat_timer.isActive():
                self.heartbeat_timer.start(30000)  # 30秒一次心跳
        except Exception as e:
            logging.error(f"启动心跳定时器失败: {e}")
            
    async def generate_auth_params(self):
        """生成认证参数"""
        timestamp = str(int(time.time()))
        # 使用机器特征生成签名
        import socket
        import getpass
        machine_id = f"{socket.gethostname()}_{getpass.getuser()}"
        
        # 简单的签名机制
        sign_string = f"{machine_id}{timestamp}"
        signature = hashlib.md5(sign_string.encode()).hexdigest()
        
        return f"machine_id={machine_id}&timestamp={timestamp}&signature={signature}"
        
    async def listen_messages(self):
        """监听代理服务器消息"""
        while self.is_connected and self.websocket:
            try:
                message = await asyncio.wait_for(
                    self.websocket.recv(),
                    timeout=30.0
                )
                
                try:
                    data = json.loads(message)
                    await self.handle_proxy_message(data)
                except json.JSONDecodeError as e:
                    self.logger.error(f"消息JSON解析失败: {e}")
                    
            except asyncio.TimeoutError:
                # 超时是正常的，继续监听
                continue
            except websockets.exceptions.ConnectionClosed as e:
                self.logger.warning(f"代理服务器连接已关闭: {e.code} - {e.reason}")
                self.is_connected = False
                self.connection_status_changed.emit("disconnected")
                self.proxy_status_changed.emit({
                    'connected': False,
                    'status': 'disconnected',
                    'message': f'连接已关闭: {e.reason}'
                })
                await self.schedule_reconnect()
                break
            except Exception as e:
                self.logger.error(f"监听消息失败: {e}")
                self.is_connected = False
                self.connection_status_changed.emit("error")
                self.proxy_status_changed.emit({
                    'connected': False,
                    'status': 'error',
                    'message': f'监听消息失败: {e}'
                })
                await self.schedule_reconnect()
                break
            
    async def handle_proxy_message(self, data):
        """处理代理服务器消息"""
        message_type = data.get('type')
        
        self.logger.info(f"[代理消息] 收到消息类型: {message_type}")
        
        if message_type == 'welcome':
            # 处理欢迎消息
            server_msg = data.get('message', '无消息')
            server_version = data.get('server_version', '未知')
            self.logger.info(f"[连接握手] 服务器欢迎消息: {server_msg}")
            self.logger.info(f"[连接握手] 服务器版本: {server_version}")
            self.connection_status_changed.emit("connected")
            self.proxy_status_changed.emit({
                'connected': True,
                'status': 'handshake_complete',
                'message': f'服务器握手完成: {server_msg}'
            })
            
            # 立即发送心跳确认
            await self.send_heartbeat()
            self.logger.info("[心跳] 已发送初始心跳")
            
        elif message_type == 'qq_message':
            # 处理QQ消息
            await self.handle_qq_message(data)
        elif message_type == 'heartbeat_ack':
            # 心跳响应
            self.logger.debug("[心跳] 收到心跳响应")
        elif message_type == 'registration_result':
            # 注册结果
            success = data.get('success', False)
            bot_id = data.get('bot_id', '未知')
            message = data.get('message', '无详情')
            qq_api_response = data.get('qq_api_response', {})
            
            self.logger.info(f"[Bot注册结果] {'成功' if success else '失败'} - BotID: {bot_id}")
            
            if success:
                self.logger.info(f"[Bot注册结果] Bot {bot_id} 已在代理服务器注册成功")
                # 发出Bot注册成功信号
                self.bot_registered.emit(bot_id, 'online')
            else:
                self.logger.error(f"[Bot注册结果] Bot {bot_id} 注册失败")
                # 发出Bot注册失败信号
                self.bot_registered.emit(bot_id, 'error')
                
        elif message_type == 'status_update':
            # 状态更新
            self.handle_status_update(data)
            status = data.get('status', '未知')
            bot_id = data.get('bot_id', '未知')
            self.logger.info(f"[状态更新] Bot {bot_id} 状态更新为: {status}")
        elif message_type == 'error':
            # 错误消息
            error_msg = data.get('message', '未知错误')
            error_code = data.get('code', '无错误码')
            self.logger.error(f"[服务器错误] 错误消息: {error_msg}")
            self.logger.error(f"[服务器错误] 错误代码: {error_code}")
            self.error_occurred.emit(error_msg)
        elif message_type == 'qq_api_response':
            # QQ官方API直接响应
            self.logger.info(f"[QQ官方API响应] 收到QQ官方API响应")
        else:
            self.logger.warning(f"[未知消息] 未知消息类型: {message_type}")
            
    async def handle_qq_message(self, data):
        """处理QQ消息"""
        try:
            message_data = {
                'bot_id': data.get('bot_id'),
                'platform': 'qq',
                'message_type': data.get('message_type', 'group_message'),
                'content': data.get('content', ''),
                'user_id': data.get('user_id'),
                'channel_id': data.get('channel_id'),
                'guild_id': data.get('guild_id'),
                'timestamp': data.get('timestamp', time.time()),
                'raw_message': json.dumps(data, ensure_ascii=False)
            }
            
            # 发出QQ消息信号
            self.qq_message_received.emit(message_data)
            self.logger.info(f"收到QQ消息: {message_data['content'][:50]}...")
            
        except Exception as e:
            self.logger.error(f"处理QQ消息失败: {e}")
            
    def handle_status_update(self, data):
        """处理状态更新"""
        status = data.get('status')
        bot_id = data.get('bot_id')
        
        if status and bot_id:
            self.logger.info(f"Bot {bot_id} 状态更新: {status}")
            # 发送状态更新信号
            self.bot_registered.emit(bot_id, status)
            
    async def send_qq_message(self, bot_id, target_id, message, message_type="group"):
        """通过代理发送QQ消息"""
        if not self.is_connected or not self.websocket:
            raise ConnectionError("未连接到代理服务器")
            
        try:
            payload = {
                'type': 'send_message',
                'bot_id': bot_id,
                'target_id': target_id,
                'message_type': message_type,
                'content': message,
                'timestamp': int(time.time())
            }
            
            await self.websocket.send(json.dumps(payload))
            logging.info(f"发送QQ消息: {message[:50]}...")
            
        except Exception as e:
            logging.error(f"发送QQ消息失败: {e}")
            raise
            
    async def register_qq_bot(self, bot_id, app_id, token, env_id=""):
        """向代理服务器注册QQ Bot"""
        if not self.is_connected or not self.websocket:
            self.logger.error(f"[Bot注册失败] 未连接到代理服务器，无法注册Bot: {bot_id}")
            raise ConnectionError("未连接到代理服务器")
            
        try:
            register_data = {
                'type': 'register_bot',
                'bot_id': bot_id,
                'platform': 'qq',
                'app_id': app_id,
                'token': token,
                'env_id': env_id,
                'timestamp': int(time.time())
            }
            
            self.logger.info(f"[Bot注册请求] 准备注册Bot: {bot_id}")
            
            await self.websocket.send(json.dumps(register_data))
            self.logger.info(f"[Bot注册请求] 注册请求已发送至代理服务器: {bot_id}")
            
        except Exception as e:
            self.logger.error(f"[Bot注册异常] 注册QQ Bot失败，BotID: {bot_id}, 异常: {e}")
            raise
            
    async def send_heartbeat(self):
        """发送心跳"""
        if not self.is_connected or not self.websocket:
            return
            
        try:
            heartbeat_data = {
                'type': 'heartbeat',
                'timestamp': int(time.time()),
                'client_id': 'library_manager'
            }
            
            await asyncio.wait_for(
                self.websocket.send(json.dumps(heartbeat_data)),
                timeout=5.0
            )
            self.logger.debug("心跳发送成功")
            
        except asyncio.TimeoutError:
            self.logger.warning("心跳发送超时")
            self.is_connected = False
            await self.schedule_reconnect()
        except Exception as e:
            self.logger.error(f"发送心跳失败: {e}")
            self.is_connected = False
            await self.schedule_reconnect()
                
    async def schedule_reconnect(self):
        """安排重连"""
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            delay = self.reconnect_delay * self.reconnect_attempts
            
            self.logger.info(f"{delay}秒后尝试第{self.reconnect_attempts}次重连...")
            self.connection_status_changed.emit(f"reconnecting_{self.reconnect_attempts}")
            self.proxy_status_changed.emit({
                'connected': False,
                'status': 'reconnecting',
                'reconnect_attempt': self.reconnect_attempts,
                'message': f'{delay}秒后尝试第{self.reconnect_attempts}次重连...'
            })
            
            # 在主线程中启动定时器
            QTimer.singleShot(delay * 1000, self.try_reconnect)
        else:
            self.logger.error("达到最大重连次数，停止重连")
            self.connection_status_changed.emit("disconnected")
            self.proxy_status_changed.emit({
                'connected': False,
                'status': 'max_reconnect_reached',
                'message': '达到最大重连次数，停止重连'
            })
            
    def try_reconnect(self):
        """尝试重连"""
        # 确保在主线程中执行
        if self.reconnect_timer.isActive():
            self.reconnect_timer.stop()
        
        # 使用线程安全的方式启动重连
        asyncio.create_task(self.connect_to_proxy())
        
    async def get_proxy_config(self):
        """获取代理服务器配置"""
        try:
            # 从现有配置系统获取代理设置
            config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "./data/api_config.json")
            
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                proxy_config = {
                    'proxy_url': config.get('proxy_server_url', ''),
                    'enabled': config.get('proxy_enabled', True)
                }
                
                return proxy_config
                
        except Exception as e:
            logging.error(f"获取代理配置失败: {e}")
            
        return None
        
    async def close(self):
        """关闭连接"""
        # 停止定时器
        if self.reconnect_timer.isActive():
            self.reconnect_timer.stop()
        if self.heartbeat_timer.isActive():
            self.heartbeat_timer.stop()
        
        self.is_connected = False
        
        if self.websocket:
            try:
                await self.websocket.close()
            except:
                pass
        if self.session:
            try:
                await self.session.close()
            except:
                pass
            
        self.connection_status_changed.emit("disconnected")
        self.proxy_status_changed.emit({
            'connected': False,
            'status': 'closed',
            'message': '连接已关闭'
        })
        
    def get_connection_status(self):
        """获取连接状态"""
        status = {
            'connected': self.is_connected,
            'proxy_url': self.proxy_url or '未配置',
            'reconnect_attempts': self.reconnect_attempts,
            'max_reconnect_attempts': self.max_reconnect_attempts
        }
        
        if self.is_connected:
            status['status_text'] = f"已连接到 {self.proxy_url}"
        elif self.reconnect_attempts > 0:
            status['status_text'] = f"连接失败，正在重试 ({self.reconnect_attempts}/{self.max_reconnect_attempts})"
        elif self.proxy_url:
            status['status_text'] = f"未连接 ({self.proxy_url})"
        else:
            status['status_text'] = "代理服务器未配置"
            
        return status
    
    # QQ官方API直接连接方法
    async def connect_qq_official_direct(self, bot_id: str, app_id: str, token: str, env_id: str = ""):
        """直接连接QQ官方API（不通过代理）"""
        try:
            # 创建QQ官方API客户端
            qq_api = QQOfficialAPI(app_id, token, env_id)
            
            # 验证凭证
            is_valid = await qq_api.validate_credentials()
            if not is_valid:
                self.error_occurred.emit(f"QQ官方API凭证验证失败: {bot_id}")
                self.bot_registered.emit(bot_id, 'error')
                return False
            
            # 保存客户端
            self.qq_api_clients[bot_id] = qq_api
            
            # 启动WebSocket监听
            websocket_task = asyncio.create_task(
                self.start_qq_websocket_listener(bot_id, qq_api)
            )
            self.qq_websocket_tasks[bot_id] = websocket_task
            
            self.logger.info(f"QQ官方API直接连接成功: {bot_id}")
            self.bot_registered.emit(bot_id, 'online')
            return True
            
        except Exception as e:
            error_msg = f"QQ官方API直接连接失败: {str(e)}"
            self.logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            self.bot_registered.emit(bot_id, 'error')
            return False
            
    async def start_qq_websocket_listener(self, bot_id: str, qq_api: QQOfficialAPI):
        """启动QQ官方WebSocket监听"""
        try:
            # 定义回调函数处理消息
            async def handle_websocket_message(data: Dict):
                # 处理不同类型的消息
                op_code = data.get('op')
                event_type = data.get('t')
                event_data = data.get('d', {})
                
                if op_code == 0:  # 事件分发
                    if event_type == 'MESSAGE_CREATE':
                        await self.handle_official_message_create(bot_id, event_data)
                    elif event_type == 'AT_MESSAGE_CREATE':
                        await self.handle_official_at_message_create(bot_id, event_data)
                        
            # 连接WebSocket
            await qq_api.connect_websocket(handle_websocket_message)
            
        except Exception as e:
            self.logger.error(f"QQ官方WebSocket监听失败: {e}")
            self.bot_registered.emit(bot_id, 'error')
            
    async def handle_official_message_create(self, bot_id: str, data: Dict):
        """处理官方消息创建事件"""
        try:
            message_data = {
                'bot_id': bot_id,
                'platform': 'qq',
                'message_type': 'group_message',
                'content': data.get('content', ''),
                'user_id': data.get('author', {}).get('id', ''),
                'user_name': data.get('author', {}).get('username', ''),
                'channel_id': data.get('channel_id', ''),
                'guild_id': data.get('guild_id', ''),
                'timestamp': datetime.now().timestamp(),
                'raw_message': json.dumps(data, ensure_ascii=False)
            }
            
            # 发出QQ消息信号
            self.qq_message_received.emit(message_data)
            self.logger.info(f"收到QQ官方消息: {message_data['content'][:50]}...")
            
        except Exception as e:
            self.logger.error(f"处理官方消息失败: {e}")
            
    async def handle_official_at_message_create(self, bot_id: str, data: Dict):
        """处理官方@消息事件"""
        try:
            message_data = {
                'bot_id': bot_id,
                'platform': 'qq',
                'message_type': 'at_message',
                'content': data.get('content', ''),
                'user_id': data.get('author', {}).get('id', ''),
                'user_name': data.get('author', {}).get('username', ''),
                'channel_id': data.get('channel_id', ''),
                'guild_id': data.get('guild_id', ''),
                'timestamp': datetime.now().timestamp(),
                'raw_message': json.dumps(data, ensure_ascii=False)
            }
            
            # 发出QQ消息信号
            self.qq_message_received.emit(message_data)
            self.logger.info(f"收到QQ官方@消息: {message_data['content'][:50]}...")
            
        except Exception as e:
            self.logger.error(f"处理官方@消息失败: {e}")
            
    async def send_qq_message_direct(self, bot_id: str, channel_id: str, content: str) -> Dict:
        """直接通过QQ官方API发送消息"""
        try:
            if bot_id not in self.qq_api_clients:
                return {'success': False, 'error': 'Bot未连接'}
            
            qq_api = self.qq_api_clients[bot_id]
            result = await qq_api.send_message(channel_id, content)
            
            if 'success' in result and not result['success']:
                self.error_occurred.emit(f"发送消息失败: {result.get('error', '未知错误')}")
                
            return result
            
        except Exception as e:
            error_msg = f"发送消息异常: {str(e)}"
            self.logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            return {'success': False, 'error': error_msg}

# 创建全局代理客户端实例
_global_proxy_client = None

def get_global_proxy_client() -> AliyunProxyClient:
    """获取全局代理客户端实例"""
    global _global_proxy_client
    if _global_proxy_client is None:
        _global_proxy_client = AliyunProxyClient()
    return _global_proxy_client


class LocalSyncHandler(http.server.BaseHTTPRequestHandler):
    """本地同步服务的 HTTP 请求处理器"""
    
    # 类变量，由服务器实例设置
    aliyun_client: Optional[AliyunClient] = None
    encryptor: Optional[ConfigEncryptor] = None
    
    def _send_response(self, status_code: int, data: Any):
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))
    
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        if path == '/ping':
            # 握手检测
            self._send_response(200, {'status': 'ok', 'service': 'aliyun-sync'})
        elif path == '/status':
            # 返回服务状态
            client = self.aliyun_client
            status = {
                'oss_connected': client.oss_client is not None and client.connected,
                'service_running': True,
                'version': '1.0'
            }
            self._send_response(200, status)
        else:
            self._send_response(404, {'error': 'Not found'})
    
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except:
            self._send_response(400, {'error': 'Invalid JSON'})
            return
        
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        if path == '/sync/compare':
            local_path = data.get('local_path')
            remote_path = data.get('remote_path')
            if not local_path or not remote_path:
                self._send_response(400, {'error': 'Missing paths'})
                return
            result = self.aliyun_client.compare_files(local_path, remote_path)
            self._send_response(200, result)
            
        elif path == '/sync/upload':
            local_path = data.get('local_path')
            remote_path = data.get('remote_path')
            encrypt = data.get('encrypt', False)
            if not local_path or not remote_path:
                self._send_response(400, {'error': 'Missing paths'})
                return
            # 可选：加密文件内容再上传
            if encrypt and self.encryptor:
                try:
                    with open(local_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    encrypted = self.encryptor.encrypt_data(content)
                    # 将加密内容写入临时文件上传
                    with tempfile.NamedTemporaryFile(delete=False, mode='w', encoding='utf-8') as tmp:
                        tmp.write(encrypted)
                        tmp_path = tmp.name
                    result = self.aliyun_client.upload_file(tmp_path, remote_path)
                    os.unlink(tmp_path)
                except Exception as e:
                    result = {'success': False, 'message': str(e)}
            else:
                result = self.aliyun_client.upload_file(local_path, remote_path)
            self._send_response(200, result)
            
        elif path == '/sync/download':
            remote_path = data.get('remote_path')
            local_path = data.get('local_path')
            decrypt = data.get('decrypt', False)
            if not remote_path or not local_path:
                self._send_response(400, {'error': 'Missing paths'})
                return
            result = self.aliyun_client.download_file(remote_path, local_path)
            if result['success'] and decrypt and self.encryptor:
                try:
                    with open(local_path, 'r', encoding='utf-8') as f:
                        encrypted = f.read()
                    decrypted = self.encryptor.decrypt_data(encrypted)
                    with open(local_path, 'w', encoding='utf-8') as f:
                        f.write(decrypted)
                except Exception as e:
                    result['message'] += f' (decryption failed: {e})'
            self._send_response(200, result)
            
        else:
            self._send_response(404, {'error': 'Not found'})

class LocalSyncServer(QObject):
    """本地同步服务器，运行在 localhost 指定端口"""
    
    status_changed = Signal(str)           # 状态变化
    error_occurred = Signal(str)           # 错误信息
    connection_check_result = Signal(dict) # 连接检查结果
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.server: Optional[socketserver.TCPServer] = None
        self.port: int = 0
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.aliyun_client = get_global_aliyun_client()
        self.encryptor = None  # 将在启动时根据配置创建
        
    def start(self, port: int = 9876) -> bool:
        """启动服务"""
        if self.running:
            return True
            
        try:
            # 创建加密器
            self.encryptor = ConfigEncryptor() if HAS_CRYPTOGRAPHY else None
            
            # 配置处理器类变量
            LocalSyncHandler.aliyun_client = self.aliyun_client
            LocalSyncHandler.encryptor = self.encryptor
            
            # 启动服务器
            self.server = socketserver.TCPServer(("127.0.0.1", port), LocalSyncHandler)
            self.port = port
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.running = True
            self.status_changed.emit(f"服务已启动在 127.0.0.1:{port}")
            return True
        except Exception as e:
            self.error_occurred.emit(f"启动服务失败: {str(e)}")
            return False
    
    def stop(self):
        """停止服务"""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.running = False
            self.status_changed.emit("服务已停止")
    
    def check_connection(self) -> Dict[str, Any]:
        """检查本地服务是否可达"""
        if not self.running:
            return {'success': False, 'message': '服务未运行'}
        try:
            response = requests.get(f'http://127.0.0.1:{self.port}/ping', timeout=2)
            if response.status_code == 200:
                return {'success': True, 'message': '本地同步服务正常'}
            else:
                return {'success': False, 'message': f'服务异常，状态码: {response.status_code}'}
        except Exception as e:
            return {'success': False, 'message': f'连接失败: {str(e)}'}
    
    def get_port(self) -> int:
        return self.port

# 全局实例
_local_sync_server = None

def get_local_sync_server() -> LocalSyncServer:
    global _local_sync_server
    if _local_sync_server is None:
        _local_sync_server = LocalSyncServer()
    return _local_sync_server

def start_local_sync_server(port: int = 9876) -> bool:
    return get_local_sync_server().start(port)

def stop_local_sync_server():
    get_local_sync_server().stop()