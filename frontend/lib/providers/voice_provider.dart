import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../services/voice_service.dart';

/// 语音交互状态
enum VoiceState { idle, recording, recognizing, speaking }

/// 语音状态管理：录音 / 识别 / 朗读 / 设置持久化
class VoiceProvider extends ChangeNotifier {
  final VoiceService _service;

  VoiceProvider({VoiceService? service}) : _service = service ?? VoiceService();

  VoiceState _state = VoiceState.idle;
  double _soundLevel = 0;
  bool _autoPlay = true;
  double _speechRate = 0.5;
  bool _sttAvailable = false;
  bool _backendAsrAvailable = false;
  bool _ttsAvailable = false;
  bool _initialized = false;
  bool _backendMode = false;
  String _error = '';

  void Function(String text, bool isFinal)? _onText;
  void Function()? _onDone;

  VoiceState get state => _state;
  bool get isRecording => _state == VoiceState.recording;
  bool get isRecognizing => _state == VoiceState.recognizing;
  bool get isSpeaking => _state == VoiceState.speaking;
  double get soundLevel => _soundLevel;
  bool get autoPlay => _autoPlay;
  double get speechRate => _speechRate;
  bool get sttAvailable => _sttAvailable;
  bool get ttsAvailable => _ttsAvailable;
  /// 是否正在走后端录音识别
  bool get usingBackendAsr => _backendMode;
  /// 是否有任何可用的语音输入方式（系统识别或后端识别）
  bool get voiceInputAvailable => _sttAvailable || _backendAsrAvailable;
  /// 是否已完成能力探测（用于区分「未探测」和「不可用」）
  bool get initialized => _initialized;
  String get error => _error;

  /// 初始化（读取设置 + 探测语音能力）
  Future<void> init() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      _autoPlay = prefs.getBool('voice_auto_play') ?? true;
      _speechRate = prefs.getDouble('voice_speech_rate') ?? 0.5;
    } catch (e) {
      debugPrint('[VoiceProvider] 读取设置失败: $e');
    }
    try {
      _sttAvailable = await _service.initStt();
      _backendAsrAvailable = await _service.initBackendAsr();
      final localTts = await _service.initTts();
      final cloudTts = await _service.initCloudTts();
      _ttsAvailable = localTts || cloudTts;
    } catch (e) {
      debugPrint('[VoiceProvider] 语音能力探测失败: $e');
    }
    _initialized = true;
    notifyListeners();
  }

  void _fail(String message) {
    _state = VoiceState.idle;
    _soundLevel = 0;
    _backendMode = false;
    _error = message;
    notifyListeners();
  }

  /// 开始录音识别：优先系统识别，不可用时走录音上传
  Future<bool> startListening({
    required void Function(String text, bool isFinal) onText,
    void Function()? onDone,
  }) async {
    await _service.stopSpeaking();
    _error = '';
    _onText = onText;
    _onDone = onDone;
    _state = VoiceState.recording;
    _soundLevel = 0;
    notifyListeners();

    // 1. 系统语音识别
    if (await _service.initStt()) {
      _sttAvailable = true;
      final started = await _service.startListening(
        onResult: onText,
        onLevel: (level) {
          if (_state != VoiceState.recording) return;
          _soundLevel = level;
          notifyListeners();
        },
        onDone: () {
          if (_state != VoiceState.recording) return;
          _state = VoiceState.idle;
          _soundLevel = 0;
          notifyListeners();
          onDone?.call();
        },
      );
      if (started) return true;
      _fail('语音识别不可用，请检查麦克风权限或系统语音服务');
      return false;
    }

    // 2. 后端识别（录音上传）
    final backendReady = await _service.initBackendAsr();
    _backendAsrAvailable = backendReady;
    if (!backendReady) {
      _fail('当前设备没有可用的语音识别服务，也未配置后端识别');
      return false;
    }
    final started = await _service.startRecording();
    if (!started) {
      _fail(_service.lastError.isEmpty ? '无法启动录音' : _service.lastError);
      return false;
    }
    _backendMode = true;
    return true;
  }

  /// 停止录音识别
  Future<void> stopListening() async {
    if (_backendMode) {
      await _stopBackendRecording();
      return;
    }
    await _service.stopListening();
    _state = VoiceState.idle;
    _soundLevel = 0;
    notifyListeners();
  }

  /// 录音结束 -> 上传后端 -> 回填文本
  Future<void> _stopBackendRecording() async {
    _backendMode = false;
    final path = await _service.stopRecording();
    if (path == null) {
      _fail('没有录到声音，请再试一次');
      return;
    }

    _state = VoiceState.recognizing;
    notifyListeners();

    try {
      final text = await _service.transcribeFile(path);
      if (text.isEmpty) {
        _fail('没有听清，请再说一次');
      } else {
        _error = '';
        _onText?.call(text, true);
      }
    } catch (e) {
      debugPrint('[VoiceProvider] 后端识别失败: $e');
      _fail('语音识别失败，请稍后重试');
    } finally {
      await _service.deleteRecording(path);
      if (_state == VoiceState.recognizing) {
        _state = VoiceState.idle;
        _soundLevel = 0;
        notifyListeners();
      }
      _onDone?.call();
    }
  }

  /// 朗读文本
  Future<void> speak(String text) async {
    if (text.trim().isEmpty) return;
    _state = VoiceState.speaking;
    notifyListeners();
    await _service.speak(text, rate: _speechRate);
    if (_state == VoiceState.speaking) {
      _state = VoiceState.idle;
      notifyListeners();
    }
  }

  /// 停止朗读
  Future<void> stopSpeaking() async {
    await _service.stopSpeaking();
    if (_state == VoiceState.speaking) {
      _state = VoiceState.idle;
      notifyListeners();
    }
  }

  /// 自动朗读开关
  Future<void> setAutoPlay(bool value) async {
    _autoPlay = value;
    notifyListeners();
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool('voice_auto_play', value);
    } catch (_) {}
  }

  /// 语速调节
  Future<void> setSpeechRate(double value) async {
    _speechRate = value;
    notifyListeners();
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setDouble('voice_speech_rate', value);
    } catch (_) {}
  }

  void clearError() {
    if (_error.isEmpty) return;
    _error = '';
    notifyListeners();
  }

  @override
  void dispose() {
    _service.dispose();
    super.dispose();
  }
}
