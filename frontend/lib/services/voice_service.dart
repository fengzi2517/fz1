import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:http/http.dart' as http;

import '../config/api_config.dart';

/// 设备端语音能力封装（M1.0）
///
/// ASR：走自研 Android 原生通道（android.speech.SpeechRecognizer），
///      不依赖第三方插件，规避插件与 AGP/Flutter 版本不兼容的问题。
/// TTS：使用 flutter_tts 调用系统语音合成。
///
/// 所有平台差异与异常都在这里吞掉，保证语音不可用时不阻塞文字对话。
class VoiceService {
  static const MethodChannel _asr = MethodChannel('ai_roleplay/asr');

  final FlutterTts _tts = FlutterTts();

  bool _sttInitialized = false;
  bool _sttAvailable = false;
  bool _ttsInitialized = false;
  bool _ttsAvailable = false;
  bool _handlersBound = false;
  bool _backendAsrChecked = false;
  bool _backendAsrAvailable = false;
  bool _cloudTtsChecked = false;
  bool _cloudTtsAvailable = false;
  Completer<void>? _speakCompleter;
  void Function()? _onTtsDone;

  void Function(String text, bool isFinal)? _onResult;
  void Function(double level)? _onLevel;
  void Function()? _onDone;
  String _lastError = '';

  bool get sttAvailable => _sttAvailable;
  bool get ttsAvailable => _ttsAvailable;
  String get lastError => _lastError;

  /// 是否支持「录音上传后端识别」（目前仅 Android）
  bool get backendAsrSupported => _isAndroid;

  /// 后端识别是否就绪（需后端配置 whisper / 云端 ASR）
  bool get backendAsrAvailable => _backendAsrAvailable;

  /// 云端 TTS 是否可用（硅基流动等）
  bool get cloudTtsAvailable => _cloudTtsAvailable;

  bool get _isAndroid =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  /// 探测语音识别能力
  Future<bool> initStt() async {
    if (!_isAndroid) {
      _sttAvailable = false;
      return false;
    }
    if (_sttInitialized) return _sttAvailable;
    _sttInitialized = true;
    try {
      _sttAvailable = await _asr.invokeMethod<bool>('isAvailable') ?? false;
    } catch (e) {
      debugPrint('[VoiceService] STT 探测失败: $e');
      _sttAvailable = false;
    }
    return _sttAvailable;
  }

  /// 初始化语音合成
  Future<bool> initTts() async {
    if (_ttsInitialized) return _ttsAvailable;
    _ttsInitialized = true;
    try {
      await _tts.setLanguage('zh-CN');
      await _tts.awaitSpeakCompletion(true);
      _ttsAvailable = true;
    } catch (e) {
      debugPrint('[VoiceService] TTS 初始化失败: $e');
      _ttsAvailable = false;
    }
    return _ttsAvailable;
  }

  void _bindHandlers() {
    if (_handlersBound) return;
    _handlersBound = true;
    _asr.setMethodCallHandler((call) async {
      switch (call.method) {
        case 'onResult':
          final args = Map<String, dynamic>.from(call.arguments as Map);
          _onResult?.call(
            (args['text'] as String?) ?? '',
            args['isFinal'] == true,
          );
          if (args['isFinal'] == true) {
            _onDone?.call();
          }
          break;
        case 'onLevel':
          _onLevel?.call((call.arguments as num).toDouble());
          break;
        case 'onError':
          final args = Map<String, dynamic>.from(call.arguments as Map);
          _lastError = (args['message'] as String?) ?? '语音识别失败';
          debugPrint('[VoiceService] ASR 错误: $_lastError');
          _onDone?.call();
          break;
        case 'onTtsDone':
          _onTtsDone?.call();
          break;
      }
    });
  }

  /// 探测云端语音合成能力
  Future<bool> initCloudTts() async {
    if (_cloudTtsChecked) return _cloudTtsAvailable;
    _cloudTtsChecked = true;
    if (!_isAndroid) return false;
    try {
      final resp = await http
          .get(Uri.parse('${ApiConfig.baseUrl}/api/tts/status'))
          .timeout(const Duration(seconds: 3));
      if (resp.statusCode == 200) {
        final data = jsonDecode(utf8.decode(resp.bodyBytes));
        _cloudTtsAvailable = data['available'] == true;
      }
    } catch (e) {
      debugPrint('[VoiceService] 云端 TTS 探测失败: $e');
      _cloudTtsAvailable = false;
    }
    return _cloudTtsAvailable;
  }

  /// 开始识别
  Future<bool> startListening({
    required void Function(String text, bool isFinal) onResult,
    void Function(double level)? onLevel,
    void Function()? onDone,
  }) async {
    if (!await initStt()) {
      _lastError = '当前设备不支持语音识别';
      return false;
    }
    _bindHandlers();
    _onResult = onResult;
    _onLevel = onLevel;
    _onDone = onDone;
    _lastError = '';
    try {
      final started = await _asr.invokeMethod<bool>('start') ?? false;
      if (!started && _lastError.isEmpty) {
        _lastError = '请允许麦克风权限后再试';
      }
      return started;
    } catch (e) {
      debugPrint('[VoiceService] 开始识别失败: $e');
      _lastError = '语音识别启动失败';
      return false;
    }
  }

  /// 停止识别（会返回最后一帧结果）
  Future<void> stopListening() async {
    try {
      await _asr.invokeMethod('stop');
    } catch (e) {
      debugPrint('[VoiceService] 停止识别失败: $e');
    }
  }

  // ---------- 录音上传后端识别（系统无语音服务时兜底）----------

  /// 探测后端识别能力
  Future<bool> initBackendAsr() async {
    if (_backendAsrChecked) return _backendAsrAvailable;
    _backendAsrChecked = true;
    if (!backendAsrSupported) return false;
    try {
      final resp = await http
          .get(Uri.parse('${ApiConfig.baseUrl}/api/asr/status'))
          .timeout(const Duration(seconds: 3));
      if (resp.statusCode == 200) {
        final data = jsonDecode(utf8.decode(resp.bodyBytes));
        _backendAsrAvailable = data['available'] == true;
      }
    } catch (e) {
      debugPrint('[VoiceService] 后端识别探测失败: $e');
      _backendAsrAvailable = false;
    }
    return _backendAsrAvailable;
  }

  /// 开始原生录音（16k 单声道 WAV）
  Future<bool> startRecording() async {
    _lastError = '';
    try {
      final ok = await _asr.invokeMethod<bool>('startRecording') ?? false;
      if (!ok) _lastError = '无法启动录音，请检查麦克风权限';
      return ok;
    } catch (e) {
      debugPrint('[VoiceService] 开始录音失败: $e');
      _lastError = '无法启动录音，请检查麦克风权限';
      return false;
    }
  }

  /// 停止录音，返回 wav 文件路径（无有效音频时返回 null）
  Future<String?> stopRecording() async {
    try {
      return await _asr.invokeMethod<String>('stopRecording');
    } catch (e) {
      debugPrint('[VoiceService] 停止录音失败: $e');
      return null;
    }
  }

  /// 取消录音并丢弃音频
  Future<void> cancelRecording() async {
    try {
      await _asr.invokeMethod('cancelRecording');
    } catch (e) {
      debugPrint('[VoiceService] 取消录音失败: $e');
    }
  }

  /// 上传音频到后端识别，返回文本
  Future<String> transcribeFile(String path) async {
    final uri = Uri.parse('${ApiConfig.baseUrl}/api/asr/transcribe');
    final req = http.MultipartRequest('POST', uri)
      ..files.add(await http.MultipartFile.fromPath('file', path));
    final streamed = await req.send().timeout(const Duration(seconds: 120));
    final body = await streamed.stream.bytesToString();
    if (streamed.statusCode != 200) {
      throw Exception('识别服务返回 ${streamed.statusCode}: $body');
    }
    final data = jsonDecode(body);
    return (data['text'] as String? ?? '').trim();
  }

  /// 删除本地临时音频
  Future<void> deleteRecording(String path) async {
    try {
      await _asr.invokeMethod('deleteRecording', {'path': path});
    } catch (_) {}
  }

  /// 朗读文本
  ///
  /// [rate] 取值 0.3~1.0，0.5 接近正常语速。
  /// 优先用云端 TTS（音色更好），不可用时回退系统 TTS。
  Future<void> speak(String text, {double rate = 0.5, double pitch = 1.0}) async {
    final content = _forSpeech(text);
    if (content.isEmpty) return;

    if (await initCloudTts()) {
      final ok = await _speakCloud(content, rate);
      if (ok) return;
    }

    if (!await initTts()) return;
    try {
      await _tts.stop();
      await _tts.setSpeechRate(rate.clamp(0.1, 1.0));
      await _tts.setPitch(pitch.clamp(0.5, 2.0));
      await _tts.speak(content);
    } catch (e) {
      debugPrint('[VoiceService] 朗读失败: $e');
    }
  }

  /// 用原生播放器流式播放后端返回的 mp3，播放结束才返回
  Future<bool> _speakCloud(String content, double rate) async {
    _bindHandlers();
    final url = Uri.parse('${ApiConfig.baseUrl}/api/tts/speech').replace(
      queryParameters: {
        'text': content,
        // 0.5（正常语速）映射到 1.0
        'speed': (rate * 2).clamp(0.5, 2.0).toStringAsFixed(2),
      },
    );

    final completer = Completer<void>();
    _speakCompleter = completer;
    _onTtsDone = () {
      if (!completer.isCompleted) completer.complete();
    };

    try {
      final started = await _asr.invokeMethod<bool>(
            'playUrl',
            {'url': url.toString()},
          ) ??
          false;
      if (!started) return false;
      // 兜底超时：避免播放器异常时状态一直停在「朗读中」
      await completer.future.timeout(const Duration(seconds: 60));
      return true;
    } catch (e) {
      debugPrint('[VoiceService] 云端朗读失败: $e');
      return false;
    } finally {
      _speakCompleter = null;
      _onTtsDone = null;
    }
  }

  /// 停止朗读
  Future<void> stopSpeaking() async {
    try {
      await _asr.invokeMethod('stopUrl');
    } catch (_) {}
    final completer = _speakCompleter;
    if (completer != null && !completer.isCompleted) completer.complete();
    if (!_ttsInitialized) return;
    try {
      await _tts.stop();
    } catch (e) {
      debugPrint('[VoiceService] 停止朗读失败: $e');
    }
  }

  /// 去掉不适合朗读的符号（动作描写、markdown 标记）
  String _forSpeech(String text) {
    var content = text;
    content = content.replaceAll(RegExp(r'[（(][^）)]*[）)]'), '');
    content = content.replaceAll(RegExp(r'[*_#>`~]'), '');
    return content.replaceAll(RegExp(r'\s+'), ' ').trim();
  }

  void dispose() {
    try {
      _asr.invokeMethod('cancel');
    } catch (_) {}
    try {
      _tts.stop();
    } catch (_) {}
  }
}
