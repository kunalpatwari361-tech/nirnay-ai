// Opens DigiLocker's sign-in page. On a phone it's a browser tab (Chrome's Auth Tab) that closes by
// itself when the engine sends it to nirnay://digilocker. On the web it's a new browser tab the
// customer closes. Either way the app then asks the engine what DigiLocker shared.
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_web_auth_2/flutter_web_auth_2.dart';
import 'package:url_launcher/url_launcher.dart';

typedef SignInOpener = Future<void> Function(String url);

/// Tests replace this with their own.
SignInOpener openSignInPage = _open;

Future<void> _open(String url) async {
  if (kIsWeb) {
    await launchUrl(Uri.parse(url), webOnlyWindowName: '_blank');
    return;
  }
  try {
    await FlutterWebAuth2.authenticate(url: url, callbackUrlScheme: 'nirnay');
  } on PlatformException {
    // The customer closed the tab. The engine still knows whether they shared anything.
  }
}
