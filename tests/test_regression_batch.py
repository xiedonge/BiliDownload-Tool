import importlib
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '工具'))
import batch_download as batch
import video_downloader as core

import tempfile
_TEMP = tempfile.TemporaryDirectory()
WORK = Path(_TEMP.name)


class BatchTests(unittest.TestCase):
    def test_multiple_urls_deduplicated_without_discovery(self):
        result = batch.enumerate_collection('BV13G41187Au\nhttps://www.bilibili.com/video/BV13G41187Au\nBV1qNi1BVEhr')
        self.assertEqual(len(result['entries']), 2)

    def test_homepages_and_oversized_list_rejected(self):
        for url in ('https://space.bilibili.com/857693', 'https://www.bilibili.com/', 'https://example.org/channel'):
            with self.assertRaises(ValueError):
                batch.enumerate_collection(url)
        with self.assertRaises(ValueError):
            batch.enumerate_collection('BV13G41187Au\nBV1qNi1BVEhr', limit=1)

    def test_exact_season_resolution_and_title_mapping(self):
        videos = ['BV13G41187Au', 'BV1qNi1BVEhr', 'BV1t5411R7wf']
        data = {'code': 0, 'data': {'ugc_season': {'id': 123, 'mid': 456, 'title': '测试合集', 'ep_count': 3,
                'sections': [{'episodes':[{'bvid':v, 'title':'测试'+str(i)} for i,v in enumerate(videos)]}]}}}
        playlist = {'_type':'playlist','title':'集合','entries':[{'id':v,'url':'https://www.bilibili.com/video/'+v} for v in videos]}
        with mock.patch('yt_dlp.YoutubeDL') as ydl, mock.patch('yt_dlp.extractor.bilibili.BiliBiliIE') as ie:
            ie.return_value._download_json.return_value = data
            instance = ydl.return_value.__enter__.return_value
            instance.extract_info.return_value = playlist
            result = batch.enumerate_collection(videos[0])
            self.assertEqual(result['expected_count'],3)
            self.assertEqual(result['entries'][1]['title'],'测试1')
            instance.extract_info.assert_called_once_with('https://space.bilibili.com/456/lists/123?type=season',download=False)
            limited = batch.enumerate_collection(videos[0],limit=2)
            self.assertTrue(limited['truncated'])
            self.assertEqual(len(limited['entries']),2)
            instance.extract_info.return_value = {**playlist,'entries':playlist['entries'][:2]}
            self.assertEqual(len(batch.enumerate_collection(videos[0])['entries']),3)
            data['data']['ugc_season']['sections'] = []
            with self.assertRaisesRegex(ValueError,'不完整'):
                batch.enumerate_collection(videos[0])

    def test_failed_item_does_not_stop_later_items(self):
        entries = [{'key':str(i),'url':str(i)} for i in range(3)]
        def download(url,*args):
            if url == '1':
                raise RuntimeError('test failure')
            return {'path':'test.mp4','reused':url == '0'}
        result = batch.run_batch(entries,WORK/'failure-test',download=download)
        self.assertEqual((result['success'],result['skipped'],result['failed'],result['pending']),(1,1,1,0))
        self.assertEqual(len(Path(result['report']).read_text(encoding='utf-8').splitlines()),3)

    def test_stop_retains_pending_and_receipt(self):
        stop = threading.Event()
        def download(url,*args):
            stop.set()
            return {'path':'test.mp4','reused':False}
        result = batch.run_batch([{'key':str(i),'url':str(i)} for i in range(3)],WORK/'stop-test',stop=stop,download=download)
        self.assertEqual((result['success'],result['pending']),(1,2))
        self.assertTrue(Path(result['report']).is_file())

    def test_folder_stable_and_preview_saved(self):
        collection = {'title':'T-ara / test','reference':'collection:1','entries':[{'url':'https://www.bilibili.com/video/BV13G41187Au'}]}
        one = batch.save_preview(collection,WORK)
        two = batch.save_preview(collection,WORK)
        self.assertEqual(one,two)
        self.assertEqual(len(list((one/'下载记录').glob('合集清单-*.json'))),2)

    def test_gui_preview_selection_and_empty_action(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        app = core.DownloaderApp(root)
        app.output_dir = WORK / 'gui'
        panel = batch.BatchWindow(app)
        panel.root.withdraw()
        panel.start()
        self.assertFalse(panel.running)
        panel.emit('parsed',{'title':'测试','reference':'test:1','truncated':False,
                            'entries':[{'id':'1','title':'视频一','url':'https://example.com/1'},
                                       {'id':'2','title':'视频二','url':'https://example.com/2'}]})
        panel.poll()
        self.assertEqual(len(panel.table.get_children()),2)
        self.assertEqual(len(panel.table.selection()),2)
        panel.set_running(True)
        self.assertTrue(panel.download_button.instate(['disabled']))
        panel.set_running(False)
        panel.close()
        root.destroy()


if __name__ == '__main__':
    unittest.main(verbosity=2)
