from flask import Flask, render_template, request, jsonify
from analyzer import QuantAnalyzer

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze_stock():
    data = request.json
    ticker = data.get('ticker')
    if not ticker:
        return jsonify({"error": "종목 코드를 입력해 주세요."}), 400

    try:
        qa = QuantAnalyzer(ticker)
        qa._fetch_micro_data()
        
        # 분석을 실행합니다.
        analysis = qa.analyze()
        
        # 분석 중에 에러가 났다면? (예: 종목이 없거나 기간이 짧음)
        if "error" in analysis:
            return jsonify(analysis), 400
            
        # 프론트엔드에 띄울 시계열 차트 데이터를 뽑아옵니다.
        chart_data = qa.get_chart_data()

        return jsonify({
            "analysis": analysis,
            "chart_data": chart_data
        })
    except Exception as e:
        return jsonify({"error": f"데이터 수집 실패: {str(e)}"}), 500

if __name__ == '__main__':
    # 로컬에서만 접근 가능한 서버를 엽니다.
    app.run(host='127.0.0.1', port=5000, debug=True)
